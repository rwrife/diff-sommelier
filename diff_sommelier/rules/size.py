"""Size / churn rule (M3).

Big hunks are simply *more to review*: a 200-line change is more likely to hide
a mistake than a 3-line one, and a high add/remove count means more surface for
a reviewer to miss something. This rule turns a hunk's changed-line count into a
graduated risk signal so large hunks float toward the top of the reading order.

It is deliberately gentle and bucketed (rather than linear) so that a genuinely
enormous hunk doesn't drown out high-confidence danger signals from the other
rules — size is a tiebreaker-ish prior, not the whole story.
"""

from __future__ import annotations

from collections.abc import Iterator

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

RULE = "size"

# (threshold of changed lines, points, label). Checked high-to-low; the first
# bucket whose threshold the hunk meets wins. Tuned so a "normal" hunk scores 0,
# a chunky one a few points, and a giant one a meaningful (but not dominating)
# amount.
_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (200, 18, "very large hunk"),
    (100, 12, "large hunk"),
    (40, 7, "sizeable hunk"),
    (15, 3, "moderate hunk"),
)


def score(hunk: Hunk, file: File) -> Iterator[Signal]:
    """Yield a size signal proportional to the hunk's changed-line count."""
    changed = hunk.changed
    for threshold, points, label in _BUCKETS:
        if changed >= threshold:
            yield Signal(
                rule=RULE,
                points=points,
                reason=f"{label}: {changed} changed lines (+{hunk.added}/-{hunk.removed})",
            )
            return
