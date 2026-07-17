"""Intent-mismatch rule (opt-in, --intent / --intent-from-pr).

Reviewers trust a PR's title and skim the diff. The hunk that quietly
contradicts the stated story — a database migration hiding inside a
"fix typo in README" PR — is exactly what slips through. Every other rule
judges a hunk on its *own* danger; this one judges it against **what the change
claims to be about**.

The idea:

1. **Take an intent string** — the PR title + body via ``--intent "..."``, or
   auto-pulled from ``gh pr view`` when ingesting a PR (:func:`intent_from_pr`).
   From it we build a set of *intent keywords*: lowercased word stems plus any
   file-path fragments the author named.

2. **Extract what each hunk actually touches** — the path fragments of the
   changed file plus the identifiers on its changed lines.

3. **Score the overlap.** If a hunk's file/identifiers share nothing with the
   stated intent, it is a *surprise*: it emits a weighted signal whose reason
   spells out the mismatch (e.g. *"touches auth/session.py but the PR is about
   'fix typo in readme'"*). Hunks that clearly relate to the intent stay quiet.

Everything here is **fully local** — no LLM, just tokenization and set overlap.
It is **opt-in**: nothing fires unless an intent string is supplied. When the
intent is empty or unusable it gracefully **no-ops** (emits no signals, never
raises), so the tool behaves exactly as before.

The public surface mirrors the other opt-in rules: :func:`make_rule` binds an
:class:`Intent` into a plain ``(Hunk, File) -> [Signal]`` rule, :func:`append_rule`
wires it into the CLI's rule list, and :func:`intent_from_pr` fetches the intent
text from the GitHub CLI for a PR number.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

__all__ = [
    "RULE",
    "Intent",
    "make_rule",
    "append_rule",
    "intent_from_pr",
]

RULE = "intent"

# Points awarded when a hunk shares *nothing* with the stated intent. A softer
# partial-mismatch tier sits below it. Chosen to be a genuine surprise signal
# (comparable to a mid danger/surface hit) without dominating a real one.
_POINTS_NO_OVERLAP = 12
_POINTS_WEAK_OVERLAP = 6

# Below this Jaccard-ish overlap ratio a hunk is "weak"; at zero it is a full
# mismatch. Tuned to stay quiet on normal, on-topic hunks.
_WEAK_OVERLAP_RATIO = 0.15

# Words that carry no topical meaning — matching on them would make almost every
# hunk look "on topic". Kept small and cross-language.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "is",
        "are",
        "be",
        "this",
        "that",
        "with",
        "from",
        "it",
        "as",
        "if",
        "add",
        "adds",
        "added",
        "fix",
        "fixes",
        "fixed",
        "update",
        "updates",
        "updated",
        "change",
        "changes",
        "changed",
        "pr",
        "prs",
        "make",
        "makes",
        "use",
        "uses",
        "new",
        "remove",
        "removes",
        "removed",
        "refactor",
        "wip",
        "chore",
        "feat",
        "docs",
        "test",
        "tests",
        "into",
        "when",
        "not",
        "no",
        "so",
        "we",
        "our",
        "you",
        "your",
        "can",
        "will",
    }
)

# Minimum token length to be a meaningful keyword.
_MIN_TOKEN_LEN = 3

# Tokenizer: alphanumeric runs (splits camelCase/snake_case at boundaries too).
_WORD = re.compile(r"[A-Za-z0-9]+")
# Split identifiers into sub-words so "getUserToken" ~ "user"/"token".
_SUBWORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _tokenize(text: str) -> set[str]:
    """Lowercase word stems from free text, minus stopwords and short words."""
    out: set[str] = set()
    for word in _WORD.findall(text):
        for sub in _SUBWORD.findall(word):
            low = sub.lower()
            if len(low) >= _MIN_TOKEN_LEN and low not in _STOPWORDS:
                out.add(low)
    return out


def _path_tokens(path: str) -> set[str]:
    """Meaningful tokens from a file path (dir/segment names, extension aside)."""
    normalized = path.replace("\\", "/")
    # Drop a trailing extension so "session.py" contributes "session", not "py".
    normalized = re.sub(r"\.[A-Za-z0-9]+$", "", normalized)
    return _tokenize(normalized)


@dataclass(frozen=True)
class Intent:
    """A parsed PR intent: the raw text and the keyword set derived from it."""

    text: str
    keywords: frozenset[str]

    @classmethod
    def parse(cls, text: str | None) -> Intent | None:
        """Build an :class:`Intent` from free text, or ``None`` if it's empty.

        Returns ``None`` when there is no usable intent (blank string, or nothing
        but stopwords), so callers can no-op cleanly.
        """
        if not text or not text.strip():
            return None
        keywords = _tokenize(text)
        if not keywords:
            return None
        return cls(text=text.strip(), keywords=frozenset(keywords))

    def _short(self, limit: int = 60) -> str:
        """A single-line, length-bounded echo of the intent for reasons."""
        one_line = " ".join(self.text.split())
        if len(one_line) <= limit:
            return one_line
        return one_line[: limit - 1].rstrip() + "…"


def _hunk_tokens(hunk: Hunk) -> set[str]:
    """What a hunk actually touches: path fragments + changed-line identifiers."""
    tokens = _path_tokens(hunk.file_path)
    for line in hunk.body.split("\n"):
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            tokens |= _tokenize(line[1:])
    return tokens


def _overlap_ratio(hunk_tokens: set[str], intent_keywords: frozenset[str]) -> float:
    """Fraction of *path-and-identifier* tokens that appear in the intent.

    Anchored on the hunk's tokens (not a symmetric Jaccard): the question is
    "how much of what this hunk touches did the author mention?", so a long PR
    description doesn't dilute the score.
    """
    if not hunk_tokens:
        return 1.0  # nothing to judge; treat as on-topic (no signal).
    shared = hunk_tokens & intent_keywords
    return len(shared) / len(hunk_tokens)


def make_rule(intent: Intent | None) -> Iterator[Signal] | object:
    """Bind ``intent`` into a ``(Hunk, File) -> [Signal]`` rule.

    Returns a rule that fires an intent-mismatch signal on hunks whose file and
    changed identifiers barely overlap the stated intent. When ``intent`` is
    ``None`` the returned rule is a permanent no-op.
    """

    def score(hunk: Hunk, file: File) -> Iterator[Signal]:
        if intent is None:
            return
        tokens = _hunk_tokens(hunk)
        ratio = _overlap_ratio(tokens, intent.keywords)
        if ratio == 0.0:
            yield Signal(
                rule=RULE,
                points=_POINTS_NO_OVERLAP,
                reason=(
                    f"touches {hunk.file_path} but the PR is about "
                    f"'{intent._short()}' — nothing here matches that story"
                ),
            )
        elif ratio < _WEAK_OVERLAP_RATIO:
            yield Signal(
                rule=RULE,
                points=_POINTS_WEAK_OVERLAP,
                reason=(
                    f"{hunk.file_path} barely relates to the stated PR intent "
                    f"'{intent._short()}' — possible scope creep"
                ),
            )

    return score


def append_rule(rules: Iterable, intent: Intent | None, *, weight=None) -> list:
    """Return ``rules`` with the intent rule appended when ``intent`` exists.

    Mirrors the other opt-in rules' wiring so the CLI stays declarative.
    ``weight`` is an optional ``(name, rule) -> rule`` wrapper (the config's
    ``apply_weight``) so a ``[weights]`` entry for ``intent`` tunes it too.
    """
    out = list(rules)
    if intent is None:
        return out
    rule = make_rule(intent)
    if weight is not None:
        rule = weight(RULE, rule)
    out.append(rule)
    return out


def intent_from_pr(pr: str, *, repo: str | None = None) -> str | None:
    """Fetch a PR's title + body via ``gh pr view`` as an intent string.

    Returns ``None`` (never raises) when ``gh`` is missing or the call fails, so
    ``--intent-from-pr`` degrades to "no intent rule" rather than an error.
    """
    if shutil.which("gh") is None:
        return None
    argv = ["gh", "pr", "view", pr, "--json", "title,body", "-q", '.title + "\\n" + .body']
    if repo:
        argv[3:3] = ["-R", repo]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None
