"""Unit tests for the blast-radius rule (issue #8).

These cover the three pieces in isolation:

* :func:`~diff_sommelier.blast_radius.extract_symbols` -- pulling changed
  definition names out of a hunk (changed lines, heading, and context-line
  definitions), across a few languages, with stopword/length filtering.
* :class:`~diff_sommelier.blast_radius.RepoIndex` -- counting references with an
  *injected* reader, so counting logic is tested without touching disk,
  including the self-file exclusion and the memoization.
* :func:`~diff_sommelier.blast_radius.make_rule` -- the ``(Hunk, File)`` rule:
  bucketing by count, ordering by impact, and the graceful no-signal cases.

End-to-end-over-git behaviour lives in ``test_cli_blast_radius.py``.
"""

from __future__ import annotations

from diff_sommelier import blast_radius as br
from diff_sommelier.parser import ChangeType, File, Hunk


def make_hunk(
    body: str,
    *,
    file_path: str = "lib/util.py",
    heading: str = "",
    added: int = 1,
    removed: int = 0,
) -> Hunk:
    """Build a hunk whose ``body`` drives symbol extraction."""
    return Hunk(
        file_path=file_path,
        old_start=1,
        old_lines=max(1, removed),
        new_start=1,
        new_lines=max(1, added),
        heading=heading,
        body=body,
        added=added,
        removed=removed,
    )


FILE = File(path="lib/util.py", change_type=ChangeType.MODIFIED)


# --------------------------------------------------------------------------- #
# extract_symbols
# --------------------------------------------------------------------------- #


def test_extracts_python_def_and_class_on_changed_lines() -> None:
    body = "-def old_name(x):\n+def new_name(x):\n+class Widget:\n     pass"
    syms = br.extract_symbols(make_hunk(body))
    assert "old_name" in syms
    assert "new_name" in syms
    assert "Widget" in syms


def test_extracts_js_const_and_export() -> None:
    body = "+export const doThing = () => 1\n+const helperFn = 2"
    syms = br.extract_symbols(make_hunk(body, file_path="app.js"))
    assert "doThing" in syms
    assert "helperFn" in syms


def test_extracts_go_func() -> None:
    body = "+func ComputeTotal(items []int) int {\n+    return 0\n+}"
    syms = br.extract_symbols(make_hunk(body, file_path="main.go"))
    assert "ComputeTotal" in syms


def test_mines_enclosing_symbol_from_heading() -> None:
    # Body-only change: the function name is only in git's @@ heading.
    body = "-    return total\n+    return total + 1"
    syms = br.extract_symbols(make_hunk(body, heading="def compute_total(items):"))
    assert "compute_total" in syms


def test_mines_definition_from_context_line() -> None:
    # Small-file body change: the def sits in an unchanged context line.
    body = " def compute_total(items):\n-    return sum(items)\n+    return sum(items) + 1"
    syms = br.extract_symbols(make_hunk(body))
    assert "compute_total" in syms


def test_context_lines_do_not_leak_bare_assignments() -> None:
    # A bare `NAME =` on a *context* line is not something this hunk changed.
    body = " total = 0\n-    return total\n+    return total + 1"
    syms = br.extract_symbols(make_hunk(body))
    assert "total" not in syms


def test_changed_line_bare_assignment_is_captured() -> None:
    body = "+MAX_RETRIES = 5"
    syms = br.extract_symbols(make_hunk(body))
    assert "MAX_RETRIES" in syms


def test_stopwords_and_short_names_dropped() -> None:
    body = "+def if_(x):\n+    id = 1\n+class Y:"
    syms = br.extract_symbols(make_hunk(body))
    # "if" is a stopword; "id"/"Y" are too short (< 3).
    assert "if" not in syms and "if_" in syms  # if_ is a real 3-char name
    assert "id" not in syms
    assert "Y" not in syms


def test_symbols_are_deduped_in_first_seen_order() -> None:
    body = "+def alpha():\n+def beta():\n+def alpha():"
    syms = br.extract_symbols(make_hunk(body))
    assert syms == ["alpha", "beta"]


def test_no_symbols_when_only_body_statements_change() -> None:
    body = "-    x = compute(a)\n+    x = compute(a, b)"
    # No definitions on changed lines, no heading, no context def -> nothing.
    assert br.extract_symbols(make_hunk(body)) == []


# --------------------------------------------------------------------------- #
# RepoIndex.count
# --------------------------------------------------------------------------- #


def _index(contents: dict[str, str]) -> br.RepoIndex:
    """Build a RepoIndex over an in-memory file map (no disk)."""
    files = list(contents)
    return br.RepoIndex(root="/repo", files=files, _read=lambda p: contents[p])


def test_count_word_boundary_matches() -> None:
    idx = _index(
        {
            "/repo/a.py": "compute_total(x)\ncompute_total(y)",
            "/repo/b.py": "# compute_total again\nprecompute_total = 1",
        }
    )
    # 2 in a.py + 1 in b.py (the `precompute_total` must NOT match on boundary).
    assert idx.count("compute_total") == 3


def test_count_excludes_own_file() -> None:
    idx = _index(
        {
            "/repo/lib/util.py": "def compute_total(): pass\ncompute_total()",
            "/repo/caller.py": "compute_total()\ncompute_total()",
        }
    )
    # Without exclusion: 2 (def-file) + 2 (caller) = 4.
    assert idx.count("compute_total") == 4
    # Excluding the def file drops its 2 self-references.
    assert idx.count("compute_total", exclude="/repo/lib/util.py") == 2


def test_count_is_memoized_per_file() -> None:
    calls: list[str] = []

    def reader(path: str) -> str:
        calls.append(path)
        return "widget widget"

    idx = br.RepoIndex(root="/repo", files=["/repo/a.py"], _read=reader)
    idx.count("widget")
    idx.count("widget")  # second call should hit the cache, not re-read
    assert calls == ["/repo/a.py"]


def test_count_zero_for_absent_symbol() -> None:
    idx = _index({"/repo/a.py": "something else"})
    assert idx.count("nowhere") == 0


# --------------------------------------------------------------------------- #
# make_rule (the (Hunk, File) rule)
# --------------------------------------------------------------------------- #


def _rule_over(contents: dict[str, str]):
    return br.make_rule(_index(contents))


def test_rule_fires_for_widely_used_symbol() -> None:
    # compute_total referenced 6 times across callers -> "several places" bucket.
    callers = {f"/repo/c{i}.py": "compute_total()" for i in range(6)}
    callers["/repo/lib/util.py"] = "def compute_total(): pass"
    rule = _rule_over(callers)

    hunk = make_hunk(
        " def compute_total(items):\n-    return sum(items)\n+    return sum(items) + 1"
    )
    signals = list(rule(hunk, FILE))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.rule == "blast-radius"
    assert sig.points == 6
    assert "compute_total" in sig.reason
    assert "6 places" in sig.reason


def test_rule_buckets_scale_with_usage() -> None:
    def points_for(n: int) -> int:
        callers = {f"/repo/c{i}.py": "sym()" for i in range(n)}
        rule = br.make_rule(_index(callers))
        hunk = make_hunk("+def sym():\n+    pass")
        sigs = list(rule(hunk, FILE))
        return sigs[0].points if sigs else 0

    # Below-5 = nothing; 5-14 = 6; 15-39 = 11; 40+ = 16.
    assert points_for(3) == 0
    assert points_for(6) == 6
    assert points_for(20) == 11
    assert points_for(45) == 16


def test_rule_orders_signals_most_impactful_first() -> None:
    contents: dict[str, str] = {}
    # `big` used a lot, `small` used a little.
    for i in range(40):
        contents[f"/repo/big{i}.py"] = "big()"
    for i in range(6):
        contents[f"/repo/small{i}.py"] = "small()"
    rule = br.make_rule(_index(contents))

    hunk = make_hunk("+def small():\n+    pass\n+def big():\n+    pass")
    signals = list(rule(hunk, FILE))
    assert len(signals) == 2
    # Highest points first regardless of definition order in the hunk.
    assert signals[0].points >= signals[1].points
    assert "big" in signals[0].reason
    assert "small" in signals[1].reason


def test_rule_no_signal_when_symbol_rarely_used() -> None:
    rule = _rule_over({"/repo/a.py": "compute_total()"})  # only 1 use
    hunk = make_hunk("+def compute_total():\n+    pass")
    assert list(rule(hunk, FILE)) == []


def test_rule_no_signal_when_no_symbols() -> None:
    rule = _rule_over({"/repo/a.py": "anything"})
    hunk = make_hunk("-    x = 1\n+    x = 2")  # no definitions anywhere
    assert list(rule(hunk, FILE)) == []


def test_singular_place_wording() -> None:
    # Exactly at the 5-threshold, phrasing uses "places"; craft a count of 5.
    callers = {f"/repo/c{i}.py": "sym()" for i in range(5)}
    rule = br.make_rule(_index(callers))
    hunk = make_hunk("+def sym():\n+    pass")
    sig = list(rule(hunk, FILE))[0]
    assert "5 places" in sig.reason
