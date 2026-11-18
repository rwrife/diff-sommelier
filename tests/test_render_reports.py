"""Portable reports: fixture snapshots, budget boundaries and safe links."""

from dataclasses import replace
from pathlib import Path

import pytest

from diff_sommelier.budget import apply_budget, parse_budget
from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_html, render_markdown
from diff_sommelier.rules import Signal
from diff_sommelier.scorer import score_diff

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/example/project/blob/abc123"


def _rows():
    return score_diff(parse_diff((ROOT / "tests" / "fixtures" / "report.patch").read_text()))


@pytest.mark.parametrize(
    ("renderer", "extension"), [(render_html, "html"), (render_markdown, "md")]
)
def test_fixture_snapshot(renderer, extension):
    rows = _rows()
    output = renderer(
        rows,
        title="Example review",
        budget=apply_budget(rows, parse_budget("1hunk")),
        repo_url=REPO_URL,
    )
    expected = (ROOT / "examples" / f"review-menu.{extension}").read_text(encoding="utf-8")
    assert output + "\n" == expected


@pytest.mark.parametrize("renderer", [render_html, render_markdown])
@pytest.mark.parametrize("spec", ["1hunk", "20hunks", "1s"])
def test_budget_cut_even_when_everything_fits(renderer, spec):
    rows = _rows()
    budget = apply_budget(rows, parse_budget(spec))
    out = renderer(rows, budget=budget)
    assert f"review {budget.cut} above" in out
    assert f"skim {budget.skimmed} below" in out
    assert out.index("auth/login.py:1") < out.index(".github/workflows/ci.yml:1")
    if renderer is render_markdown and budget.skimmed:
        assert out.index("<details>") < out.index(".github/workflows/ci.yml:1")
        assert "Below budget cut" in out
        assert "(low risk)" not in out


@pytest.mark.parametrize("renderer", [render_html, render_markdown])
def test_empty_and_unbudgeted_reports(renderer):
    assert "Nothing to taste" in renderer([])
    out = renderer(_rows())
    assert "cut:" not in out
    assert "auth/login.py:1" in out
    assert "eval/exec" in out
    assert "CI gate" in renderer(_rows(), fail_over=1)


@pytest.mark.parametrize("renderer", [render_html, render_markdown])
def test_escapes_untrusted_text_and_quotes_link_paths(renderer):
    row = _rows()[0]
    row = replace(
        row,
        hunk=replace(row.hunk, file_path="dir/a <script>|[x](y)#?.py", new_start=0),
        signals=[Signal("test", 0, '<script>alert("x")</script> | [link](bad)')],
    )
    out = renderer([row], title="<script>title</script>", repo_url=REPO_URL)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "/dir/a%20%3Cscript%3E%7C%5Bx%5D%28y%29%23%3F.py#L1" in out
    assert "(+0)" not in out


@pytest.mark.parametrize("renderer", [render_html, render_markdown])
@pytest.mark.parametrize("url", ["javascript:alert(1)", "//example.com", "https://x/a#b"])
def test_rejects_unsafe_link_bases(renderer, url):
    with pytest.raises(ValueError, match="repo-url"):
        renderer(_rows(), repo_url=url)
