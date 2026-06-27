# diff-sommelier 🍷

**Triage your code-review attention.** `diff-sommelier` reads a diff — yours, or the 2,000-line dump your AI agent just produced — scores every hunk by how **risky** and **surprising** it is, and tells you the optimal **order to read it**. Spend your five real minutes of attention on the hunks that can actually hurt you; skim the rest.

It is deliberately **read-only**: it never splits, stages, commits, or rewrites anything. It just answers one question the commit-splitter tools ignore:

> *"I have 7 minutes — what do I read first, and what can I safely skim?"*

## Why

The AI-agent era means humans now review far more machine-generated code than they can carefully read. Most new git/AI tools optimize the **author** side (split a mess into clean commits). diff-sommelier optimizes the **reviewer** side: a transparent, local, heuristic **attention budget** for diffs.

## Status

🚧 Early. **M1 (scaffold)** and **M2 (typed diff parser)** are done: an
installable CLI with `--version`, a stdin file/hunk counter, and a robust
unified-diff parser (`diff_sommelier.parser`) that turns any diff into typed
`File`/`Hunk` objects with stable content-hash hunk IDs. The real risk scoring
lands in M3 — see [`PLAN.md`](./PLAN.md) for the roadmap (M1–M6).

```bash
# Install (editable) and try it
pip install -e .
diff-sommelier --version
git diff | diff-sommelier          # -> "Parsed N files, M hunks."
```

The parser is also usable directly:

```python
from diff_sommelier import parse_diff

diff = parse_diff(open("changes.patch").read())
for hunk in diff.hunks:
    print(hunk.id, hunk.file_path, f"+{hunk.added}/-{hunk.removed}", hunk.header)
```

## Planned usage (v0.1)

```bash
# From a PR
gh pr diff 123 | diff-sommelier

# From git
diff-sommelier --range main..HEAD
diff-sommelier --staged

# From a file, with a time budget and a CI gate
diff-sommelier -f changes.patch --budget 5m --fail-over 80 --json
```

Output: a ranked "tasting menu" of hunks, most-risky-first, each with a 0–100 score, a one-line **why**, and its `file:line` — plus a cut line showing what to review vs. skim.

## Tech

Python 3.11+, stdlib-only core, `rich` for the terminal UI, `pytest` + `ruff`. 100% local heuristics in v0.1 — no LLM, no network required.

## License

MIT (see `LICENSE`).
