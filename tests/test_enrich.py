"""Tests for the opt-in LLM enrichment layer (:mod:`diff_sommelier.enrich`).

These exercise the pure pieces with **injectable backends** so nothing ever
touches a network: a stub backend records the batch it received and returns
canned notes, and the offline :class:`~diff_sommelier.enrich.EchoBackend` covers
the deterministic default. The contract under test mirrors issue #7: only the
top-N hunks are sent, in a single batched call, and notes come back as
zero-point, clearly-labelled signals that never move the score or the ranking.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from diff_sommelier.enrich import (
    DEFAULT_TOP_N,
    LLM_RULE,
    Backend,
    EchoBackend,
    EnrichmentError,
    HunkRequest,
    attach_notes,
    build_requests,
    enrich,
    resolve_backend,
)
from diff_sommelier.parser import parse_diff
from diff_sommelier.scorer import score_diff


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _scored(diff_text: str):
    """Parse + score a diff, returning the most-risky-first ScoredHunk list."""
    return score_diff(parse_diff(diff_text))


# A two-file diff: an auth+eval hunk (high risk) and a README hunk (zero risk),
# so the ranking is deterministic and we can assert *which* hunks get enriched.
_TWO_HUNKS = (
    "diff --git a/auth/login.py b/auth/login.py\n"
    "--- a/auth/login.py\n"
    "+++ b/auth/login.py\n"
    "@@ -10,3 +10,4 @@ def login(u, p):\n"
    "-    if check(u, p):\n"
    "+    if check(u, p) or u == 'admin':\n"
    "+        eval(p)\n"
    "         return token(u)\n"
    "diff --git a/README.md b/README.md\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@ -1,2 +1,3 @@\n"
    " # Title\n"
    "+A new line.\n"
)


class _StubBackend:
    """Records the batch it was handed and returns caller-supplied notes."""

    def __init__(self, notes: dict[str, str] | None = None, *, per_id: bool = False):
        self._notes = notes or {}
        self._per_id = per_id
        self.seen: list[HunkRequest] = []
        self.calls: int = 0

    def explain(self, requests: Sequence[HunkRequest]) -> dict[str, str]:
        self.calls += 1
        self.seen = list(requests)
        if self._per_id:
            return dict(self._notes)
        # Same note for every requested hunk when not keyed by id.
        return {r.id: next(iter(self._notes.values()), "note") for r in requests}


class _BoomBackend:
    """A backend that always fails, to test error normalization."""

    def explain(self, requests: Sequence[HunkRequest]) -> dict[str, str]:
        raise RuntimeError("kaboom")


# --------------------------------------------------------------------------- #
# EchoBackend is a real Backend (structural typing) and is offline
# --------------------------------------------------------------------------- #
def test_echo_backend_satisfies_protocol():
    assert isinstance(EchoBackend(), Backend)


def test_echo_backend_notes_every_request_with_counts():
    scored = _scored(_TWO_HUNKS)
    reqs = build_requests(scored, top_n=2)
    notes = EchoBackend().explain(reqs)
    assert set(notes) == {r.id for r in reqs}
    # The auth hunk added 2 and removed 1 line; echo reports the tally.
    auth_id = next(r.id for r in reqs if r.file_path == "auth/login.py")
    assert "+2/-1" in notes[auth_id]


# --------------------------------------------------------------------------- #
# build_requests: only the top-N, carrying identity + body
# --------------------------------------------------------------------------- #
def test_build_requests_slices_top_n():
    scored = _scored(_TWO_HUNKS)
    assert len(scored) == 2
    reqs = build_requests(scored, top_n=1)
    assert len(reqs) == 1
    # The riskiest (auth) hunk is first, so it's the one sent.
    assert reqs[0].file_path == "auth/login.py"
    assert reqs[0].id == scored[0].hunk.id
    assert reqs[0].score == scored[0].score


def test_build_requests_zero_and_negative_top_n_send_nothing():
    scored = _scored(_TWO_HUNKS)
    assert build_requests(scored, top_n=0) == []
    assert build_requests(scored, top_n=-5) == []


def test_build_requests_top_n_beyond_length_is_clamped():
    scored = _scored(_TWO_HUNKS)
    reqs = build_requests(scored, top_n=99)
    assert len(reqs) == len(scored)


# --------------------------------------------------------------------------- #
# attach_notes: additive, labelled, zero-point, non-mutating
# --------------------------------------------------------------------------- #
def test_attach_notes_appends_zero_point_labelled_signal():
    scored = _scored(_TWO_HUNKS)
    top = scored[0]
    before_raw = top.raw
    before_signals = len(top.signals)

    enriched = attach_notes(scored, {top.hunk.id: "could deadlock under load"})
    got = enriched[0]

    # A new signal was appended; it's the LLM rule, zero points, labelled.
    assert len(got.signals) == before_signals + 1
    note = got.signals[-1]
    assert note.rule == LLM_RULE
    assert note.points == 0
    assert note.reason.startswith("model: ")
    assert "could deadlock under load" in note.reason
    # Score/raw are untouched — the model explains, it does not re-score.
    assert got.score == top.score
    assert got.raw == before_raw


def test_attach_notes_does_not_mutate_input():
    scored = _scored(_TWO_HUNKS)
    original_len = len(scored[0].signals)
    attach_notes(scored, {scored[0].hunk.id: "note"})
    assert len(scored[0].signals) == original_len  # input untouched


def test_attach_notes_ignores_missing_and_blank_notes():
    scored = _scored(_TWO_HUNKS)
    enriched = attach_notes(scored, {scored[0].hunk.id: "   "})  # whitespace only
    # No signal added for a blank note; the hunk passes through unchanged.
    assert len(enriched[0].signals) == len(scored[0].signals)
    # And a hunk with no entry at all is unchanged too.
    assert len(enriched[1].signals) == len(scored[1].signals)


def test_attach_notes_collapses_whitespace():
    scored = _scored(_TWO_HUNKS)
    enriched = attach_notes(scored, {scored[0].hunk.id: "line one\n   line two\t\tend"})
    assert enriched[0].signals[-1].reason == "model: line one line two end"


# --------------------------------------------------------------------------- #
# enrich: end-to-end with an injected backend (single batched call)
# --------------------------------------------------------------------------- #
def test_enrich_sends_only_top_n_in_a_single_call():
    scored = _scored(_TWO_HUNKS)
    backend = _StubBackend({"x": "watch the auth path"})
    enriched = enrich(scored, backend=backend, top_n=1)

    # Exactly one backend call, carrying exactly the top-1 hunk.
    assert backend.calls == 1
    assert len(backend.seen) == 1
    assert backend.seen[0].file_path == "auth/login.py"
    # The note landed on the top hunk; the un-sent hunk is unchanged.
    assert enriched[0].signals[-1].rule == LLM_RULE
    assert all(s.rule != LLM_RULE for s in enriched[1].signals)


def test_enrich_preserves_ranking_order():
    scored = _scored(_TWO_HUNKS)
    order_before = [s.hunk.id for s in scored]
    backend = _StubBackend({scored[0].hunk.id: "a", scored[1].hunk.id: "b"}, per_id=True)
    enriched = enrich(scored, backend=backend, top_n=2)
    assert [s.hunk.id for s in enriched] == order_before


def test_enrich_empty_diff_short_circuits_without_backend():
    # No hunks → no backend needed, even if none is configured.
    assert enrich([], top_n=DEFAULT_TOP_N) == []


def test_enrich_top_n_zero_short_circuits_without_backend():
    scored = _scored(_TWO_HUNKS)
    # top_n=0 means "send nothing", so it must not try to resolve a backend.
    out = enrich(scored, top_n=0)
    assert [s.hunk.id for s in out] == [s.hunk.id for s in scored]
    assert all(all(sig.rule != LLM_RULE for sig in s.signals) for s in out)


def test_enrich_wraps_backend_failure():
    scored = _scored(_TWO_HUNKS)
    with pytest.raises(EnrichmentError, match="kaboom"):
        enrich(scored, backend=_BoomBackend(), top_n=1)


def test_enrich_rejects_non_mapping_result():
    class Bad:
        def explain(self, requests):
            return ["not", "a", "dict"]

    with pytest.raises(EnrichmentError, match="non-mapping"):
        enrich(_scored(_TWO_HUNKS), backend=Bad(), top_n=1)


def test_enrich_with_echo_backend_offline():
    scored = _scored(_TWO_HUNKS)
    enriched = enrich(scored, backend=EchoBackend(), top_n=2)
    assert enriched[0].signals[-1].rule == LLM_RULE
    assert "[echo]" in enriched[0].signals[-1].reason


# --------------------------------------------------------------------------- #
# resolve_backend: env-keyed, with a clear error when unconfigured
# --------------------------------------------------------------------------- #
def test_resolve_backend_unconfigured_raises_actionable_error():
    with pytest.raises(EnrichmentError) as exc:
        resolve_backend(env={})
    msg = str(exc.value)
    assert "SOMMELIER_LLM_BACKEND" in msg
    assert "echo" in msg  # points the user at a working value


def test_resolve_backend_unknown_backend_raises():
    with pytest.raises(EnrichmentError, match="unknown LLM backend"):
        resolve_backend(env={"SOMMELIER_LLM_BACKEND": "gpt-nope"})


def test_resolve_backend_echo_is_case_insensitive():
    assert isinstance(resolve_backend(env={"SOMMELIER_LLM_BACKEND": "  ECHO "}), EchoBackend)


def test_enrich_uses_env_backend_when_none_injected():
    scored = _scored(_TWO_HUNKS)
    enriched = enrich(scored, top_n=1, env={"SOMMELIER_LLM_BACKEND": "echo"})
    assert enriched[0].signals[-1].rule == LLM_RULE


def test_enrich_unconfigured_backend_raises_when_work_to_do():
    scored = _scored(_TWO_HUNKS)
    with pytest.raises(EnrichmentError, match="no backend is configured"):
        enrich(scored, top_n=1, env={})
