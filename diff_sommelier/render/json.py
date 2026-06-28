"""JSON presenter (the machine contract).

This is the ``--json`` output: a JSON array of scored hunks, most-risky-first,
each with its stable id, file, line range, 0-100 score, raw points, and the
signals (rule + points + reason) that produced the score. It is the canonical
contract for agents, editors, and the later ``--budget`` / ``--fail-over``
tooling, so its shape is intentionally stable and defined by
:meth:`diff_sommelier.scorer.ScoredHunk.to_dict`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from diff_sommelier.scorer import ScoredHunk

__all__ = ["render_json"]


def render_json(scored: Iterable[ScoredHunk], *, indent: int | None = 2) -> str:
    """Serialize scored hunks to a JSON array string."""
    return json.dumps([s.to_dict() for s in scored], indent=indent)
