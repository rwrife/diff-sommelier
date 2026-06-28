# diff-sommelier 🍷

**Triage your code-review attention.** `diff-sommelier` reads a diff — yours, or the 2,000-line dump your AI agent just produced — scores every hunk by how **risky** and **surprising** it is, and tells you the optimal **order to read it**. Spend your five real minutes of attention on the hunks that can actually hurt you; skim the rest.

It is deliberately **read-only**: it never splits, stages, commits, or rewrites anything. It just answers one question the commit-splitter tools ignore:

> *"I have 7 minutes — what do I read first, and what can I safely skim?"*

## Why

The AI-agent era means humans now review far more machine-generated code than they can carefully read. Most new git/AI tools optimize the **author** side (split a mess into clean commits). diff-sommelier optimizes the **reviewer** side: a transparent, local, heuristic **attention budget** for diffs.

## Status

🚧 Early, but the engine is live. **M1–M4 are done:** an installable CLI, a
robust unified-diff parser (`diff_sommelier.parser`) that turns any diff into
typed `File`/`Hunk` objects with stable content-hash hunk IDs, a transparent
**heuristic scoring engine** (`diff_sommelier.rules` + `diff_sommelier.scorer`)
that scores every hunk **0–100** with explainable signals, and the human
**tasting menu** — a ranked, colour-coded terminal view (`diff_sommelier.render`).
The attention budget and CI gate land in M5+ — see [`PLAN.md`](./PLAN.md) for the
roadmap (M1–M6).

```bash
# Install (editable) and try it
pip install -e .
diff-sommelier --version
git diff | diff-sommelier          # -> ranked "tasting menu" (most risky first)
git diff | diff-sommelier --json   # -> scored, explained hunks as JSON
```

### The tasting menu (default)

Pipe any unified diff in and `diff-sommelier` prints a ranked menu: each hunk
gets a risk **tier** (`SAVR` skim-safe · `SIP` read it · `GULP` read this
first), a 0–100 score with a bar, its `file:line`, and the one-line **why**.
It's colour-coded in a terminal and degrades to clean plain text when piped or
with `--no-color`.

```text
🍷 diff-sommelier — 3 hunks across 3 files · top risk 92

   #  TIER  SCR  RISK                    WHY
────────────────────────────────────────────────────────────────────────────
   1  GULP   92  [##################  ]  auth/login.py:1  adds a hardcoded
                                         secret-looking literal (+18); adds
                                         dynamic eval/exec (+16); touches
                                         authentication/session code (+14)
   2  SIP    41  [########            ]  .github/workflows/ci.yml:1  touches
                                         CI workflow (+10)
   3  SAVR    0  [                    ]  README.md:1  (no notable signals)

Tiers: GULP (read first, ≥60) · SIP (read, ≥25) · SAVR (skim-safe, <25).
```

### Scoring (`--json`)

`--json` emits a JSON array of hunks ordered most-risky-first. Every point on a
score is traceable to a named rule and a one-line reason — there is no LLM and
no magic:

```jsonc
[
  {
    "id": "3206ecc81fc0",
    "file": "auth/login.py",
    "old_start": 1, "new_start": 1,
    "added": 4, "removed": 1,
    "score": 92,
    "raw": 48,
    "signals": [
      { "rule": "danger",  "points": 18, "reason": "adds a hardcoded secret-looking literal" },
      { "rule": "danger",  "points": 16, "reason": "adds dynamic eval/exec" },
      { "rule": "surface", "points": 14, "reason": "touches authentication/session code" }
    ]
  }
]
```

The v0.1 rule pack:

- **size** — large hunks / high churn are simply more to review.
- **surface** — touches auth/crypto, DB migrations, CI workflows, Dockerfiles,
  dependency manifests/lockfiles, or env/credential config.
- **danger** — deletions, dynamic `eval`/`exec`, shell/subprocess calls,
  hardcoded secrets & private keys, disabled TLS verification, loosened CORS,
  permission/privilege changes, and raw SQL.

Add a rule by dropping a `Hunk -> [Signal]` function into
`diff_sommelier/rules/` and registering it — that's the extension surface for
the rest of the backlog.

The parser is also usable directly:

```python
from diff_sommelier import parse_diff

diff = parse_diff(open("changes.patch").read())
for hunk in diff.hunks:
    print(hunk.id, hunk.file_path, f"+{hunk.added}/-{hunk.removed}", hunk.header)
```

## Planned usage (M5–M6)

The menu works on any diff today; these ergonomics are still on the roadmap:

```bash
# Direct git / PR ingestion (M6) instead of piping
diff-sommelier --range main..HEAD
diff-sommelier --staged

# A time/size budget cut line + a CI gate (M5)
diff-sommelier -f changes.patch --budget 5m --fail-over 80
```

The budget adds a cut line to the menu (review above it, skim below), and
`--fail-over <score>` makes CI exit non-zero when a hunk no one may have read
is scarier than the threshold.

## Tech

Python 3.11+, stdlib-only core, `rich` for the terminal UI, `pytest` + `ruff`. 100% local heuristics in v0.1 — no LLM, no network required.

## License

MIT (see `LICENSE`).
