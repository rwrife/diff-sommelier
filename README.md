# diff-sommelier 🍷

**Triage your code-review attention.** `diff-sommelier` reads a diff — yours, or the 2,000-line dump your AI agent just produced — scores every hunk by how **risky** and **surprising** it is, and tells you the optimal **order to read it**. Spend your five real minutes of attention on the hunks that can actually hurt you; skim the rest.

It is deliberately **read-only**: it never splits, stages, commits, or rewrites anything. It just answers one question the commit-splitter tools ignore:

> *"I have 7 minutes — what do I read first, and what can I safely skim?"*

## Why

The AI-agent era means humans now review far more machine-generated code than they can carefully read. Most new git/AI tools optimize the **author** side (split a mess into clean commits). diff-sommelier optimizes the **reviewer** side: a transparent, local, heuristic **attention budget** for diffs.

## Status

🚧 Early, but the engine is live. **M1–M5 are done:** an installable CLI, a
robust unified-diff parser (`diff_sommelier.parser`) that turns any diff into
typed `File`/`Hunk` objects with stable content-hash hunk IDs, a transparent
**heuristic scoring engine** (`diff_sommelier.rules` + `diff_sommelier.scorer`)
that scores every hunk **0–100** with explainable signals, the human
**tasting menu** — a ranked, colour-coded terminal view (`diff_sommelier.render`)
— and the **attention budget + CI gate** (`diff_sommelier.budget`): a `--budget`
cut line and a `--fail-over` exit code. Git/PR ingestion and a config file land
in M6 — see [`PLAN.md`](./PLAN.md) for the roadmap (M1–M6).

```bash
# Install (editable) and try it
pip install -e .
diff-sommelier --version
git diff | diff-sommelier              # -> ranked "tasting menu" (most risky first)
git diff | diff-sommelier --budget 5m  # -> menu with a "review above / skim below" cut
git diff | diff-sommelier --json       # -> scored, explained hunks as JSON
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

### Attention budget + CI gate

**`--budget`** draws a cut line in the menu: review the hunks above it, skim the
rest. Express it as **time** (`5m`, `90s`, `1m30s`) or a **count** (`10hunks`,
or a bare integer). Hunks are charged a simple, configurable reading-time
model — a per-hunk overhead plus a per-changed-line cost — and spent
most-risky-first, so your minutes go to the dangerous hunks. The single
scariest hunk is always kept above the line even if it alone blows the budget
(and the cut line shows the honest estimate):

```text
🍷 diff-sommelier — 3 hunks across 3 files · top risk 92

   #  TIER  SCR  RISK                    WHY
────────────────────────────────────────────────────────────────────────────
   1  GULP   92  [##################  ]  auth/login.py:1  adds a hardcoded
                                         secret-looking literal (+18); adds
                                         dynamic eval/exec (+16)
─ cut: review 1 above · skim 2 below · budget 20s · ≈14s above ──────────────
   2  SIP    38  [########            ]  db/migrate.py:10  adds raw SQL (+9)
   3  SAVR    0  [                    ]  README.md:1  (no notable signals)
```

**`--fail-over <score>`** makes the process exit non-zero (status `1`) when any
hunk's risk score is **≥** the threshold — a one-line CI gate against a scary
hunk slipping through unreviewed. Because scores are absolute, a threshold
means the same thing on every run. It composes with `--json`:

```bash
# Fail the build if any hunk scores 80+ (the menu still prints; exit is non-zero)
git diff origin/main... | diff-sommelier --fail-over 80

# Same gate, machine-readable, in a pipeline
git diff origin/main... | diff-sommelier --json --fail-over 80 > review.json
```

The reading-time model lives in `diff_sommelier.budget.TimeModel`
(`seconds_per_changed_line`, `per_hunk_overhead_s`) and is exposed on the API
today; a `.sommelier.toml` to tune it from the CLI arrives in M6.

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

## Planned usage (M6)

The menu, budget, and CI gate work on any piped diff today; these ingestion
ergonomics are still on the roadmap:

```bash
# Direct git / PR ingestion (M6) instead of piping
diff-sommelier --range main..HEAD
diff-sommelier --staged

# Tune rule weights and the reading-time model from a config file (M6)
diff-sommelier -f changes.patch --budget 5m --fail-over 80   # works today via stdin
```

## Tech

Python 3.11+, stdlib-only core, `rich` for the terminal UI, `pytest` + `ruff`. 100% local heuristics in v0.1 — no LLM, no network required.

## License

MIT (see `LICENSE`).
