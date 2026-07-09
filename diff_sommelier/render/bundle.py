"""Context-budget bundle renderer (issue #25 — AI-reviewer bundles).

The whole reason diff-sommelier exists: **AI reviewers fall apart on big
diffs.** Hand a model a 1,000-line dump and its context window overflows,
coherence collapses, and it degrades into style nitpicks. diff-sommelier
already knows *which* hunks matter most — this renderer produces the fix.

``render_bundle`` packs the **highest-risk hunks, most-dangerous-first**, into a
**token-bounded, paste-ready review prompt**: a short preamble telling the model
what it's looking at, then, per included hunk, its ``file:line``, the one-line
*why* (the rules that fired), and the raw hunk body — stopping the moment the
token (or hunk) budget is hit, with a trailer reporting how many lower-risk
hunks were omitted.

It's the machine-side sibling of the human ``--budget 5m`` cut line
(:mod:`diff_sommelier.budget`): same *"spend attention where it counts"* idea,
but the consumer is an AI reviewer with a **context limit** instead of a human
with a **time limit**. It reuses the scorer's most-risky-first order and the
shared *savor / sip / gulp* tier vocabulary (:mod:`.tiers`).

The token count is a deliberately **dependency-free approximation** — a
``chars / 4`` heuristic (:func:`estimate_tokens`), the common rule of thumb for
English/code — so there's no tokenizer dependency and the estimate is stable and
offline. Like the other presenters this **builds** the prompt and does no I/O
and makes **no network/LLM call**; sending it stays the user's choice, keeping
the project's "AI is opt-in" stance intact.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from diff_sommelier.render.tiers import tier_for
from diff_sommelier.scorer import ScoredHunk

__all__ = [
    "render_bundle",
    "estimate_tokens",
    "parse_context_budget",
    "ContextBudget",
    "ContextBudgetError",
    "CHARS_PER_TOKEN",
]

# A single newline, defined once so the string builders below never need a
# backslash escape inline (keeps the raw-body fencing unambiguous).
_NL = chr(10)

# Approximate characters per token. Four is the widely-cited rule of thumb for
# English/code across common BPE tokenizers; we use it so the ``tok`` budget can
# be honoured with **zero dependencies** (no tokenizer install). It is
# intentionally approximate and documented as such — a safety margin, not a
# guarantee, so a real reviewer's context window isn't blown by a small miss.
CHARS_PER_TOKEN = 4


class ContextBudgetError(ValueError):
    """Raised when a ``--context-budget`` spec can't be parsed."""


@dataclass(frozen=True)
class ContextBudget:
    """A parsed context budget for the AI-reviewer bundle.

    Exactly one dimension is set: an approximate **token** cap (``tokens``) or a
    **count** of hunks (``hunks``). Build one with :func:`parse_context_budget`.
    """

    tokens: int | None = None
    hunks: int | None = None

    @property
    def is_tokens(self) -> bool:
        return self.tokens is not None

    @property
    def is_count(self) -> bool:
        return self.hunks is not None


# ``6000tok`` / ``6000tokens`` / ``6000t`` -> a token cap.
_TOKEN_RE = re.compile(r"^\s*(\d+)\s*(?:t|tok|toks|tokens?)\s*$", re.IGNORECASE)
# ``8hunks`` / ``8hunk`` -> a hunk count.
_COUNT_RE = re.compile(r"^\s*(\d+)\s*(?:hunks?)\s*$", re.IGNORECASE)
# A bare integer -> a hunk count (mirrors ``--budget``'s bare-int convention).
_BARE_INT_RE = re.compile(r"^\s*(\d+)\s*$")


def parse_context_budget(spec: str) -> ContextBudget:
    """Parse a ``--context-budget`` string into a :class:`ContextBudget`.

    Accepted forms (case-insensitive)::

        "6000tok"    -> 6000 tokens (approximate)
        "6000t"      -> 6000 tokens
        "6000tokens" -> 6000 tokens
        "8hunks"     -> 8 hunks
        "8hunk"      -> 8 hunks
        "8"          -> 8 hunks   (a bare integer means hunks)

    Raises :class:`ContextBudgetError` on anything else.
    """
    if spec is None:
        raise ContextBudgetError("empty context budget")
    text = spec.strip()
    if not text:
        raise ContextBudgetError("empty context budget")

    m = _TOKEN_RE.match(text)
    if m:
        return ContextBudget(tokens=_positive(m.group(1), text))

    m = _COUNT_RE.match(text)
    if m:
        return ContextBudget(hunks=_positive(m.group(1), text))

    m = _BARE_INT_RE.match(text)
    if m:
        return ContextBudget(hunks=_positive(m.group(1), text))

    raise ContextBudgetError(
        f"unrecognized context budget {spec!r}; use e.g. '6000tok' or '8hunks'"
    )


def _positive(digits: str, spec: str) -> int:
    n = int(digits)
    if n <= 0:
        raise ContextBudgetError(f"context budget must be positive: {spec!r}")
    return n


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` (dependency-free).

    Uses a ``ceil(len(text) / CHARS_PER_TOKEN)`` heuristic — no tokenizer
    dependency, stable and offline. Documented as approximate: it is a safety
    margin for a reviewer's context window, not an exact accounting.
    """
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil division


def _location(hunk) -> str:
    """``file:line`` for a hunk, using the new-file start line."""
    return f"{hunk.file_path}:{hunk.new_start}"


def _reason_note(signal) -> str:
    """Format one signal for the one-line *why*.

    A scoring signal shows its points (``reason (+7)``); a zero-point signal is
    a non-scoring note (e.g. an opt-in ``model:`` note) and renders without the
    misleading ``(+0)`` — mirroring the text/markdown renderers.
    """
    if signal.points > 0:
        return f"{signal.reason} (+{signal.points})"
    return signal.reason


def _why(scored: ScoredHunk) -> str:
    """The one-line reasons for a hunk, most-impactful first."""
    if not scored.signals:
        return "no notable signals — skim-safe"
    return "; ".join(_reason_note(s) for s in scored.signals)


def _hunk_block(scored: ScoredHunk, *, ordinal: int) -> str:
    """Render one hunk's section of the bundle: header, why, and raw body.

    The raw ``@@`` header + body are included verbatim so the reviewer sees the
    exact change (with surrounding context lines), fenced as a diff for models
    and humans alike.
    """
    tier = tier_for(scored.score)
    hunk = scored.hunk
    header = f"### {ordinal}. {_location(hunk)} — {tier.value.name} (risk {scored.score})"
    why = f"why: {_why(scored)}"
    # Reconstruct the canonical hunk header line, then the raw body exactly as
    # parsed (bodies keep their trailing newline; strip only the final one so
    # the fence closes cleanly). Built with _NL joins to avoid inline escapes.
    body = hunk.body.rstrip(_NL)
    fence_open = "```diff"
    fence_close = "```"
    fenced = _NL.join([fence_open, hunk.header, body, fence_close])
    return _NL.join([header, why, "", fenced])


def _preamble(n_total: int, *, title: str | None) -> list[str]:
    """The bundle's opening instructions for the AI reviewer."""
    lines = [
        "# Code review — highest-risk hunks first",
        "",
        (
            "Review the following diff hunks, **in order** — they are ranked "
            "most-risky-first by diff-sommelier's heuristics (size, changed "
            "surface, and dangerous patterns). Each hunk lists *why* it was "
            "flagged. Focus your attention on the earlier, higher-risk hunks; "
            "call out real bugs, security issues, and risky changes."
        ),
    ]
    if title:
        lines += ["", f"**Stated intent:** {title.strip()}"]
    return lines


def _trailer(included: int, total: int, omitted: int) -> str:
    """The closing line reporting coverage and how many hunks were omitted."""
    if omitted <= 0:
        return f"_All {total} hunk{'s' if total != 1 else ''} fit within the budget._"
    hunk_word = "hunk" if omitted == 1 else "hunks"
    return (
        f"_Budget reached: showing the {included} highest-risk of {total} hunks; "
        f"{omitted} lower-risk {hunk_word} omitted._"
    )


def _select(
    scored: Sequence[ScoredHunk],
    budget: ContextBudget,
) -> tuple[list[ScoredHunk], list[str]]:
    """Pick the hunks that fit the budget and pre-render their blocks.

    ``scored`` is expected most-risky-first. For a **count** budget we take the
    first ``hunks`` rows. For a **token** budget we add hunk blocks in rank
    order while the cumulative token estimate stays within the cap — but the
    single highest-risk hunk is **always** included even if it alone exceeds the
    budget, because dropping the scariest hunk for being long would defeat the
    entire purpose (mirrors :func:`budget.apply_budget`).

    Returns the included hunks and their rendered blocks (parallel lists).
    """
    rows = list(scored)
    if budget.is_count:
        cut = min(budget.hunks or 0, len(rows))
        chosen = rows[:cut]
        blocks = [_hunk_block(s, ordinal=i) for i, s in enumerate(chosen, start=1)]
        return chosen, blocks

    # Token budget: charge each block (plus a small joiner allowance) and stop
    # when the running estimate would exceed the cap.
    limit = budget.tokens or 0
    chosen: list[ScoredHunk] = []
    blocks: list[str] = []
    running = 0
    for i, s in enumerate(rows):
        block = _hunk_block(s, ordinal=i + 1)
        cost = estimate_tokens(block) + 2  # +2 for the blank-line joiner
        if i == 0:
            # Always include the top-ranked (most dangerous) hunk.
            chosen.append(s)
            blocks.append(block)
            running += cost
            continue
        if running + cost <= limit:
            chosen.append(s)
            blocks.append(block)
            running += cost
        else:
            break
    return chosen, blocks


def render_bundle(
    scored: Sequence[ScoredHunk],
    *,
    budget: ContextBudget,
    title: str | None = None,
) -> str:
    """Render a token-bounded, paste-ready review bundle for ``scored`` hunks.

    ``scored`` is expected most-risky-first (as :func:`score_diff` returns).
    ``budget`` (from :func:`parse_context_budget`) caps the bundle by
    approximate **tokens** or a **hunk count**. The output is a Markdown prompt:
    a preamble (optionally naming the ``title`` / stated intent), then the
    selected hunks — each with ``file:line``, its one-line *why*, and the raw
    hunk body — stopping at the budget, and a trailer reporting how many
    lower-risk hunks were omitted.

    Returns a string and performs no I/O and **no** network/LLM call: it only
    *builds* the prompt. Newline-joined, no trailing newline, so the caller owns
    final spacing.
    """
    rows = list(scored)
    total = len(rows)

    if total == 0:
        return _NL.join(
            [
                *_preamble(0, title=title),
                "",
                "_No hunks in this diff — nothing to review._",
            ]
        )

    chosen, blocks = _select(rows, budget)
    included = len(chosen)
    omitted = total - included

    parts: list[str] = [*_preamble(total, title=title), ""]
    parts.append((_NL + _NL).join(blocks))
    parts.append("")
    parts.append(_trailer(included, total, omitted))
    return _NL.join(parts)
