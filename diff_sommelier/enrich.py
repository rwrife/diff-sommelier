"""Optional LLM enrichment for the riskiest hunks (opt-in, --explain-llm).

The heuristic rule pack (:mod:`diff_sommelier.rules`, plus the opt-in
:mod:`~diff_sommelier.blast_radius` / :mod:`~diff_sommelier.hotspots` rules) is
and remains the source of truth: it is fast, free, offline, and every point on a
hunk's score traces back to a named rule. This module adds a *strictly optional*
layer on top — after scoring, it can send only the **top-N riskiest hunks** to a
model and ask "what could break here?", then fold the model's answer back in as
extra, clearly-labelled reasons.

Design contract (issue #7):

* **Disabled by default.** Nothing here runs unless the CLI is given
  ``--explain-llm``. With the flag absent the tool is 100% local/offline and this
  module is never imported by the hot path.
* **Only the top-N hunks are sent.** :func:`enrich` takes the already-ranked
  list (most-risky-first) and slices the first ``top_n`` (default
  :data:`DEFAULT_TOP_N`, configurable). The rest of the diff is never sent to a
  backend — the whole diff never leaves the machine.
* **Pluggable, env-keyed backend.** A backend is anything with an
  ``explain(requests) -> notes`` method (see :class:`Backend`). :func:`resolve_backend`
  builds one from environment variables and raises :class:`EnrichmentError` with
  a clear, actionable message when nothing is configured — so an unconfigured
  ``--explain-llm`` fails loudly instead of silently doing nothing.
* **Notes are additive and labelled.** A model note becomes a :class:`~diff_sommelier.rules.Signal`
  with rule :data:`LLM_RULE` and **zero points**, so it shows up alongside the
  heuristic reasons (and in ``--json``) but never moves the 0-100 score. The
  heuristics decide risk; the model only *explains*.
* **Cost-aware batching.** All N hunks are packed into a **single**
  :meth:`Backend.explain` call, so a run costs one request regardless of N.

The public surface mirrors the opt-in rules
(:mod:`~diff_sommelier.hotspots`): a small dataclass model, a backend protocol,
an environment resolver, and one :func:`enrich` entry point the CLI calls after
:func:`~diff_sommelier.scorer.score_diff`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from diff_sommelier.rules import Signal
from diff_sommelier.scorer import ScoredHunk

__all__ = [
    "LLM_RULE",
    "DEFAULT_TOP_N",
    "EnrichmentError",
    "HunkRequest",
    "Backend",
    "EchoBackend",
    "build_requests",
    "attach_notes",
    "resolve_backend",
    "enrich",
]

# Rule name stamped on every model-generated Signal. Distinct from the heuristic
# rule names so renderers (and anyone reading --json) can tell a model note from
# a heuristic reason, and so a future ``[weights]`` entry could tune it. Notes
# always carry zero points, so weighting them is a no-op today by design.
LLM_RULE = "llm"

# How many of the top-ranked hunks get sent to the backend when the caller does
# not specify. Small on purpose: enrichment targets the handful of hunks a
# reviewer would read first, not the whole diff.
DEFAULT_TOP_N = 3

# Prefix on every model note's reason text, so the human menu and the JSON make
# it unmistakable that the words came from a model, not a deterministic rule.
_NOTE_PREFIX = "model: "


class EnrichmentError(RuntimeError):
    """LLM enrichment was requested but could not run.

    Raised for an unconfigured/unknown backend or a backend-side failure, so the
    CLI can report it on stderr with a non-zero exit rather than silently
    skipping the enrichment the user explicitly asked for.
    """


@dataclass(frozen=True)
class HunkRequest:
    """One unit of work sent to a :class:`Backend`: identity + the diff to judge.

    Attributes:
        id: The stable hunk id (from :class:`~diff_sommelier.parser.Hunk`), used
            to route the returned note back to the right hunk.
        file_path: The hunk's file path, given to the model for context.
        header: The ``@@ -a,b +c,d @@`` header line, for line context.
        body: The raw hunk body (the ``+``/``-`` lines) — what the model reads.
        score: The heuristic 0-100 score, passed through so a backend can prompt
            with "reviewers already flagged this as risky" if it wants.
    """

    id: str
    file_path: str
    header: str
    body: str
    score: int


@runtime_checkable
class Backend(Protocol):
    """A pluggable model backend.

    A backend receives the batch of top-N :class:`HunkRequest` objects and
    returns a mapping of ``hunk id -> note text`` for the hunks it has something
    to say about. It may omit a hunk (no note) and must not raise for a hunk it
    simply has no opinion on. Any hard failure (auth, network) should raise
    :class:`EnrichmentError` so the CLI reports it cleanly.

    The single-call shape is deliberate: it lets implementations batch all N
    hunks into one request to respect cost/rate limits.
    """

    def explain(self, requests: Sequence[HunkRequest]) -> dict[str, str]:
        """Return ``{hunk_id: note}`` for some/all ``requests`` (batched)."""
        ...


@dataclass(frozen=True)
class EchoBackend:
    """A deterministic, offline backend used for tests and ``--explain-llm`` demos.

    It contacts no network and simply echoes a templated observation about each
    hunk (size and location). Its value is twofold: it lets the enrichment plumbing
    be exercised end-to-end without credentials or flakiness, and it gives
    ``SOMMELIER_LLM_BACKEND=echo`` as a zero-setup way to *see* how model notes
    render before wiring a real provider. It is intentionally not a real model.
    """

    label: str = "echo"

    def explain(self, requests: Sequence[HunkRequest]) -> dict[str, str]:
        notes: dict[str, str] = {}
        for req in requests:
            lines = req.body.splitlines()
            added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
            notes[req.id] = (
                f"[{self.label}] {req.file_path}: review this change "
                f"(+{added}/-{removed} lines); check edge cases and error handling."
            )
        return notes


def build_requests(scored: Sequence[ScoredHunk], top_n: int) -> list[HunkRequest]:
    """Slice the top ``top_n`` scored hunks into :class:`HunkRequest` payloads.

    ``scored`` must be most-risky-first (as :func:`~diff_sommelier.scorer.score_diff`
    returns), so the slice is genuinely the riskiest hunks. ``top_n`` is clamped
    to ``>= 0``; ``0`` yields an empty batch (nothing is sent). Only these
    payloads ever reach a backend — the remainder of the diff is not included.
    """
    n = max(0, top_n)
    out: list[HunkRequest] = []
    for s in scored[:n]:
        h = s.hunk
        out.append(
            HunkRequest(
                id=h.id,
                file_path=h.file_path,
                header=h.header,
                body=h.body,
                score=s.score,
            )
        )
    return out


def _note_signal(note: str) -> Signal:
    """Wrap a model note as a zero-point, clearly-labelled :class:`Signal`.

    Zero points is the whole point: the note rides along with the heuristic
    reasons and appears in every renderer and in ``--json``, but contributes
    nothing to the 0-100 score. Whitespace is collapsed to a single line so a
    chatty model can't smear the menu across many rows.
    """
    text = " ".join(note.split())
    return Signal(rule=LLM_RULE, points=0, reason=f"{_NOTE_PREFIX}{text}")


def attach_notes(
    scored: Iterable[ScoredHunk],
    notes: dict[str, str],
) -> list[ScoredHunk]:
    """Return ``scored`` with model ``notes`` merged in as extra signals.

    For each hunk whose id has a note, a zero-point :data:`LLM_RULE` signal is
    appended to that hunk's signal list (a new frozen :class:`ScoredHunk` is
    produced; the input is not mutated). Hunks without a note pass through
    untouched, and empty/whitespace-only notes are ignored. Because the notes are
    zero-point, neither the per-hunk score nor the overall ranking changes — so
    the enriched list stays in the same most-risky-first order it came in.
    """
    out: list[ScoredHunk] = []
    for s in scored:
        note = notes.get(s.hunk.id)
        if note and note.strip():
            merged = [*s.signals, _note_signal(note)]
            out.append(replace(s, signals=merged))
        else:
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Backend resolution (environment-keyed)
# --------------------------------------------------------------------------- #
# Environment variable naming the backend to use. Kept provider-agnostic: the
# only backend shipped in this slice is the offline ``echo`` one; real providers
# (env-keyed API backends) are a follow-up, but the resolver and the clear
# "unconfigured" error are in place so wiring one is additive.
_BACKEND_ENV = "SOMMELIER_LLM_BACKEND"

# Registry of built-in backend factories, keyed by the value of _BACKEND_ENV.
# A factory takes the environment mapping (so future backends can read their own
# keys, e.g. an API token) and returns a :class:`Backend`.
_BACKENDS: dict[str, object] = {
    "echo": lambda env: EchoBackend(),
}


def resolve_backend(env: dict[str, str] | None = None) -> Backend:
    """Build the configured :class:`Backend` from the environment.

    Reads :data:`_BACKEND_ENV` to pick a backend. Raises :class:`EnrichmentError`
    with an actionable message when the variable is unset (the "clear error if
    unconfigured" contract) or names a backend we do not know. ``env`` is
    injectable for tests; it defaults to :data:`os.environ`.

    Only the offline ``echo`` backend ships in this slice. It is a real,
    deterministic backend (no network), which is exactly what makes the
    enrichment path testable and demoable without credentials; provider-backed
    backends slot into :data:`_BACKENDS` without touching callers.
    """
    env = dict(os.environ if env is None else env)
    name = (env.get(_BACKEND_ENV) or "").strip().lower()
    if not name:
        known = ", ".join(sorted(_BACKENDS))
        raise EnrichmentError(
            "LLM enrichment requested but no backend is configured. "
            f"Set {_BACKEND_ENV} to one of: {known}. "
            f"(Try {_BACKEND_ENV}=echo for a local, offline demo backend.)"
        )
    factory = _BACKENDS.get(name)
    if factory is None:
        known = ", ".join(sorted(_BACKENDS))
        raise EnrichmentError(
            f"unknown LLM backend {name!r} (set {_BACKEND_ENV} to one of: {known})."
        )
    return factory(env)  # type: ignore[operator]


def enrich(
    scored: Sequence[ScoredHunk],
    *,
    backend: Backend | None = None,
    top_n: int = DEFAULT_TOP_N,
    env: dict[str, str] | None = None,
) -> list[ScoredHunk]:
    """Enrich the top-N scored hunks with model notes and return the new list.

    This is the single entry point the CLI calls when ``--explain-llm`` is set.
    Steps:

    1. Slice the top ``top_n`` hunks (only these are sent anywhere).
    2. Resolve the backend from the environment (unless one is injected), which
       raises :class:`EnrichmentError` when unconfigured.
    3. Call :meth:`Backend.explain` **once** with the whole batch (cost-aware).
    4. Merge the returned notes back as zero-point, labelled signals.

    On an empty diff or ``top_n <= 0`` it short-circuits and returns ``scored``
    unchanged without resolving a backend, so "enable enrichment but nothing to
    do" never errors. Any exception from the backend is wrapped as an
    :class:`EnrichmentError` so the CLI has a single failure type to report.
    """
    requests = build_requests(scored, top_n)
    if not requests:
        return list(scored)
    active = resolve_backend(env) if backend is None else backend
    try:
        notes = active.explain(requests)
    except EnrichmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize any backend failure
        raise EnrichmentError(f"LLM backend failed: {exc}") from exc
    if not isinstance(notes, dict):  # defensive: a misbehaving backend
        raise EnrichmentError("LLM backend returned a non-mapping result.")
    return attach_notes(scored, notes)
