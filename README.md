# diff-sommelier 🍷

**Triage your code-review attention.** `diff-sommelier` reads a diff — yours, or the 2,000-line dump your AI agent just produced — scores every hunk by how **risky** and **surprising** it is, and tells you the optimal **order to read it**. Spend your five real minutes of attention on the hunks that can actually hurt you; skim the rest.

It is deliberately **read-only**: it never splits, stages, commits, or rewrites anything. It just answers one question the commit-splitter tools ignore:

> *"I have 7 minutes — what do I read first, and what can I safely skim?"*

## Why

The AI-agent era means humans now review far more machine-generated code than they can carefully read. Most new git/AI tools optimize the **author** side (split a mess into clean commits). diff-sommelier optimizes the **reviewer** side: a transparent, local, heuristic **attention budget** for diffs.

## Status

🚧 Early, but the whole v0.1 pipeline is live. **M1–M6 are done:** an
installable CLI, a robust unified-diff parser (`diff_sommelier.parser`) that
turns any diff into typed `File`/`Hunk` objects with stable content-hash hunk
IDs, a transparent **heuristic scoring engine** (`diff_sommelier.rules` +
`diff_sommelier.scorer`) that scores every hunk **0–100** with explainable
signals, the human **tasting menu** — a ranked, colour-coded terminal view
(`diff_sommelier.render`) — the **attention budget + CI gate**
(`diff_sommelier.budget`): a `--budget` cut line and a `--fail-over` exit code,
and **git/PR ergonomics**: `--staged`, `--range A..B`, clean `gh pr diff`
ingestion, and a `.sommelier.toml` for custom rule weights and surface paths.
An opt-in **`--blast-radius`** flag cross-references changed symbols against the
rest of the repo, so a *tiny* edit to a widely-used function gets flagged, and
**`--hotspots`** mines `git log` to boost hunks in historically bug-prone files
(high churn, often fixed), and **`--owners`** reads `CODEOWNERS` to boost hunks
in files owned by someone other than the PR author (or unowned entirely). Two v0.2+ backlog items have also landed as strictly
optional layers: **`--explain-llm`** sends only the top-N riskiest hunks
to a model for extra, clearly-labelled notes (off by default; the core stays
100% local), a **GitHub Action** posts a self-updating *review-order menu*
comment on every PR (a `--markdown` renderer under the hood), **`--sarif`**
emits a SARIF 2.1.0 log so the ranked hunks surface as inline **code-scanning
annotations** (tier drives the SARIF level), and **`--context-budget`** packs
the highest-risk hunks into a token-bounded, paste-ready **review bundle for AI
reviewers** (the machine-side sibling of the human `--budget` cut line).
See [`PLAN.md`](./PLAN.md) for the roadmap (M1–M6) and v0.2+ backlog.

```bash
# Install (editable) and try it
pip install -e .
diff-sommelier --version
git diff | diff-sommelier              # -> ranked "tasting menu" (most risky first)
diff-sommelier --staged                # -> the same, straight from the git index
diff-sommelier --range main..HEAD      # -> what a PR added vs. main
git diff | diff-sommelier --budget 5m  # -> menu with a "review above / skim below" cut
git diff | diff-sommelier --json       # -> scored, explained hunks as JSON
git diff | diff-sommelier --sarif      # -> SARIF 2.1.0 for code-scanning annotations
git diff | diff-sommelier --context-budget 6000tok  # -> token-bounded bundle for an AI reviewer
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
today.

### Blast radius (`--blast-radius`)

A two-line change to a function imported in forty files is high-risk *precisely
because* it looks tiny — it sails past a reviewer while quietly touching the
whole codebase. **`--blast-radius`** catches exactly that: it extracts the
symbols a hunk defines/changes (or the function it lives inside), counts how
often they're referenced across the rest of the working tree, and adds a
weighted signal proportional to that reach.

```bash
diff-sommelier --staged --blast-radius
git diff | diff-sommelier --blast-radius --json
```

```
#  TIER  SCR  RISK                    WHY
1  SIP    57  [###########       ]  lib/util.py:1  blast radius: 'compute_total'
                                    reverberates across the codebase (40 places in the repo)
```

It's **opt-in** and fully **local/offline** — just a filesystem scan. It prefers
your git-tracked files (so it honours `.gitignore`) and falls back to a bounded
directory walk when git isn't available. Outside a repo (nothing to scan) it
gracefully **no-ops**. Symbol extraction is a conservative, language-agnostic
regex pass today (Python/JS/TS/Go and friends); tree-sitter precision is a later
backlog item. Tune or mute it like any rule via `[weights]` (key: `blast-radius`).

### Hotspots (`--hotspots`)

Files that change constantly — and keep getting *fixed* — are where bugs breed.
Michael Feathers called these **hotspots**. **`--hotspots`** mines your
`git log` once for per-file **churn** (how many commits touched it) and **fix
frequency** (how many of those commits looked like fixes: `fix`, `bug`,
`revert`, `regression`…), then adds a weighted signal to hunks in the busiest,
most-repeatedly-broken files — so a *one-line* tweak to a file with a bad
history floats up your reading order.

```bash
diff-sommelier --staged --hotspots
git diff | diff-sommelier --hotspots --json
```

```
#  TIER  SCR  RISK                    WHY
1  SIP    53  [###########       ]  app/core.py:1  hotspot: file changes very
                                    frequently and is repeatedly fixed (37 commits, 14 fixes)
```

"Hot" is scaled **relative to your busiest file** (so the signal means the same
in a small repo and a huge one), with a small absolute floor so a brand-new repo
doesn't light everything up. A high **fix ratio** bumps the score: a file that
is not just busy but *repeatedly broken* is the real danger. It's **opt-in** and
fully **local/offline** (just `git log`), history is read once and cached, and
outside a git repo (or with no history) it gracefully **no-ops**. Tune or mute
it like any rule via `[weights]` (key: `hotspots`).

### Ownership (`--owners`)

A hunk is riskier when it touches code **you don't own** — and riskiest of all
when a file has **no owner at all** (nobody's watching it). **`--owners`** reads
your repo's `CODEOWNERS` and boosts hunks whose files are owned by *someone
other than the PR author*, or that match no CODEOWNERS entry. It's the *social*
risk axis, complementing the structural (`--blast-radius`) and historical
(`--hotspots`) ones.

```bash
git diff | diff-sommelier --owners --author @your-handle
diff-sommelier --staged --owners --author @octocat --json
```

```
#  TIER  SCR  RISK                    WHY
1  SIP    41  [########          ]  src/api/pay.py:1  owned by @team-payments,
                                    not the author
2  TASTE  32  [######            ]  infra/legacy.tf:1  no CODEOWNERS entry — unowned file
```

Pass **`--author`** with the PR author's CODEOWNERS handle (e.g. `@octocat`, or a
team like `@org/team`); files the author already owns are skipped. CODEOWNERS is
discovered from the three standard locations (`.github/CODEOWNERS`,
`CODEOWNERS`, `docs/CODEOWNERS`, first found wins), with glob patterns and
GitHub's **last-match-wins** precedence. Unowned files get a slightly higher
bump than other-owned ones. It's **opt-in** and fully **local/offline** (just a
file parse); with no CODEOWNERS file or no `--author` it gracefully **no-ops**.
Tune or mute it like any rule via `[weights]` (key: `owners`).

### LLM enrichment (`--explain-llm`, opt-in)

The heuristics are the source of truth: fast, free, offline, and every point on a
score traces back to a named rule. **`--explain-llm`** adds a *strictly optional*
layer on top: after ranking, it sends only the **top-N riskiest hunks** to a
model, asks "what could break here?", and folds the answer back in as extra,
clearly-labelled reasons. Heuristics still decide risk — the model only
*explains*.

```bash
# Off by default. Pick a backend via an env var; 'echo' is a local, offline demo.
SOMMELIER_LLM_BACKEND=echo git diff | diff-sommelier --explain-llm
SOMMELIER_LLM_BACKEND=echo git diff | diff-sommelier --explain-llm --explain-llm-top 5 --json
```

```
#  TIER  SCR  RISK                    WHY
1  GULP   80  [################    ]  auth/login.py:10  adds dynamic eval/exec (+16);
                                    touches authentication/session code (+14);
                                    model: review the new admin bypass and eval() path
```

The contract is deliberately conservative:

- **Disabled by default** — without the flag the tool is 100% local/offline and
  never loads the enrichment code.
- **Only the top-N are sent** — `--explain-llm-top N` (default `3`) bounds it;
  the rest of the diff never leaves your machine.
- **One batched call** — all N hunks go in a single request, so a run costs one
  call regardless of N (respecting cost/rate limits).
- **Notes are additive and labelled** — each model note shows up as a `model:`
  reason (rule `llm` in `--json`) with **zero points**, so it never moves the
  0-100 score or the ranking.
- **Backend is env-keyed, with a clear error if unconfigured** — `--explain-llm`
  without `SOMMELIER_LLM_BACKEND` set fails loudly (exit `2`) instead of silently
  doing nothing. The offline `echo` backend ships today so you can see how notes
  render with zero setup; provider-backed backends slot in without changing how
  you call the tool.

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

### SARIF for code scanning (`--sarif`)

A comment nobody expands is easy to ignore. **`--sarif`** emits a
[SARIF 2.1.0](https://sarifweb.azurewebsites.net/) log so the ranked hunks show
up as **inline annotations** exactly where reviewers already look — the PR
**"Files changed"** view, the repo **Security → Code scanning** tab, and any
SARIF-aware IDE (the VS Code SARIF Viewer, etc.).

The risk **tier drives the SARIF level**, so the annotations agree with the
tasting menu's colours:

| Tier | SARIF `level` | Shows up as |
|---|---|---|
| 🔴 gulp | `error` | a red code-scanning error |
| 🟡 sip | `warning` | a yellow warning |
| 🟢 savor | `note` | a low-key note |

Each hunk becomes one `result`: its `physicalLocation` is the file + the
post-image line range, `message.text` is the same one-line *why* the menu shows,
`ruleId` is the dominant firing rule (with a `rules[]` catalog in
`tool.driver`), and `properties` carries the 0-100 `score` and the stable hunk
id. Output is deterministic (stable order, no timestamps).

```bash
git diff origin/main... | diff-sommelier --sarif > diff-sommelier.sarif
# --sarif is a JSON output mode (mutually exclusive with --json/--markdown);
# it still honours --fail-over (exit code) and --title (recorded in the log).
```

Drop this step into a workflow to publish the annotations (works alongside the
review-order comment):

```yaml
- name: diff-sommelier -> SARIF
  run: |
    git fetch --no-tags --depth=1 origin "${{ github.base_ref }}"
    git diff "origin/${{ github.base_ref }}..." \
      | diff-sommelier --sarif --title "${{ github.event.pull_request.title }}" \
      > diff-sommelier.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: diff-sommelier.sarif
    category: diff-sommelier
```

> Needs `permissions: security-events: write` on the job so the upload can
> post code-scanning results.

### Context budget for AI reviewers (`--context-budget`)

The whole reason diff-sommelier exists: **AI reviewers fall apart on big
diffs.** A 1,000-line dump overflows the context window, coherence collapses,
and the model degrades into style nitpicks. diff-sommelier already knows *which*
hunks matter most — **`--context-budget`** produces the fix: a token-bounded,
paste-ready **review bundle** of only the highest-risk hunks, most-dangerous-
first, so you hand your LLM reviewer the handful of hunks that can actually hurt
you instead of the whole diff.

It's the machine-side sibling of the human [`--budget`](#attention-budget--ci-gate)
cut line: same *"spend attention where it counts"* idea, but the consumer is an
AI reviewer with a **context limit** instead of a human with a **time limit**.
The budget is either an approximate **token** cap (`6000tok`) or a **hunk**
count (`8hunks`, or a bare integer). Token counting is a deliberately
dependency-free `chars / 4` approximation — documented as approximate, a safety
margin for the context window, not an exact tokenizer.

```bash
# Build the bundle and pipe it straight to your reviewer of choice:
git diff origin/main... | diff-sommelier --context-budget 6000tok > review.md
# or cap by hunk count, and name the PR so the preamble states the intent:
git diff | diff-sommelier --context-budget 8hunks --title "Add SSO login"
```

The bundle is a Markdown prompt: a short preamble ("review these in order, here's
why each was flagged"), then per included hunk its `file:line`, the one-line
*why* (the rules that fired), and the raw hunk body — selection stops at the
budget, most-risky-first, and a trailer reports how many lower-risk hunks were
omitted. It's a JSON-free output mode (mutually exclusive with
`--json`/`--markdown`/`--sarif`) and still honours `--fail-over` (exit code).
**No network or LLM call is made** — it only *builds* the prompt; sending it
stays your choice, consistent with diff-sommelier's "AI is opt-in" stance.

## Real repos & PRs

Three ways to feed `diff-sommelier` a diff — all produce the same menu, budget,
JSON, and CI gate:

```bash
# 1. Pipe anything on stdin (git, a .patch file, or a PR):
git diff | diff-sommelier
diff-sommelier < changes.patch
gh pr diff 123 | diff-sommelier            # review a GitHub PR by number

# 2. The git index (what you've staged):
diff-sommelier --staged

# 3. A git range (what a branch/PR adds):
diff-sommelier --range main..HEAD
diff-sommelier --range origin/main...      # the merge-base form `git diff` understands
```

`--staged` and `--range` shell out to `git` for you (no piping needed) and run
in the current repo. They're mutually exclusive; with neither, the diff is read
from stdin.

## GitHub Action (review-order menu on every PR)

Bring the tasting menu to where review actually happens. The bundled action
runs `diff-sommelier` on each pull request and posts **one comment** — a
reading-order checklist of the diff's hunks, most-risky-first, with the
skim-safe ones tucked into a collapsed section. It **updates that same comment**
on new pushes (no comment spam), and can optionally **fail a status check** when
a hunk is scarier than a threshold.

Drop this in as `.github/workflows/review-menu.yml`
(see [`examples/github-action-workflow.yml`](./examples/github-action-workflow.yml)):

```yaml
name: Review menu
on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read        # read the code
  pull-requests: write  # post/update the menu comment + status

jobs:
  review-menu:
    runs-on: ubuntu-latest
    steps:
      # Only needed for --blast-radius / --hotspots (they scan the tree/history).
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: rwrife/diff-sommelier@v0.1.0
        with:
          fail-over: "80"     # optional: fail the check when a hunk scores >= 80
          blast-radius: "true"
          hotspots: "true"
```

The comment looks like a menu you can tick through:

```markdown
## 🍷 diff-sommelier — review-order menu

**7 hunks** across **4 files** · top risk **88**

### Read these first
_In recommended reading order, most-risky-first._

| | # | Tier | Score | Location | Why |
|---|---:|---|---:|---|---|
| [ ] | 1 | 🔴 gulp | 88 | `auth/login.py:10` | adds dynamic eval/exec (+16); touches authentication/session code (+14) |
| [ ] | 2 | 🟡 sip  | 34 | `db/migrate.py:12` | edits a database migration (+12) |

<details><summary>🟢 Skim-safe · 5 hunks (low risk)</summary>
... collapsed ...
</details>
```

### Inputs

| Input | Default | What it does |
|---|---|---|
| `fail-over` | _(off)_ | Also emit a **failing status check** when any hunk's score is `>=` this value. |
| `blast-radius` | `false` | Pass `--blast-radius` (needs the repo checked out). |
| `hotspots` | `false` | Pass `--hotspots` (needs `fetch-depth: 0` for full history). |
| `config` | _(auto)_ | Path to a `.sommelier.toml` for custom rule weights/paths. |
| `python-version` | `3.12` | Python used to run the CLI. |
| `github-token` | `${{ github.token }}` | Token for reading the diff and posting the comment (`pull-requests: write`). |
| `ref` | _(this action's ref)_ | Git ref/version of diff-sommelier to install. |

### Outputs

| Output | What it is |
|---|---|
| `score` | The highest hunk risk score in the diff (0–100). |
| `hunks` | Total number of hunks in the diff. |
| `comment-url` | Link to the posted/updated review-menu comment. |

Under the hood the action is just the CLI: it pipes `gh pr diff` into
`diff-sommelier --markdown` (which honours `--fail-over` and `--blast-radius` /
`--hotspots`), so the comment says exactly what the terminal would. You can
reproduce it locally with `gh pr diff <n> | diff-sommelier --markdown`.

## Config (`.sommelier.toml`)

Drop a `.sommelier.toml` at your repo root to tune scoring for your codebase.
`diff-sommelier` discovers it by walking up from the working directory; pass
`--config PATH` to point at a specific file, or `--no-config` to ignore it.

```toml
# Re-weight a rule's influence. 1.0 = default, 0 mutes it, 2.0 doubles it.
# Reasons are unchanged — only the points (and the 0–100 score) move.
[weights]
size    = 0.5    # we don't care much about big-but-boring hunks
danger  = 1.5    # but really want eval/exec/secrets to float to the top
# "blast-radius" = 0   # (opt-in rule) mute or amplify it too, e.g. 2.0
# hotspots       = 2.0 # (opt-in rule) lean harder on bug-prone files

# Mark extra paths as "dangerous by location", on top of the built-ins
# (auth, crypto, migrations, CI, Dockerfiles, deps...). Each entry needs a
# Python regex `pattern` (matched case-insensitively), `points`, and a `reason`.
[[surface]]
pattern = "(^|/)payments/"
points  = 14
reason  = "touches the payments module"

[[surface]]
pattern = "(^|/)infra/terraform/"
points  = 12
reason  = "touches infrastructure-as-code"
```

Known rule names for `[weights]` are `size`, `surface`, `danger`,
`blast-radius`, and `hotspots` (the same names you see under `"rule"` in
`--json`; `blast-radius` and `hotspots` only fire when you pass their opt-in
flags).

## Tech

Python 3.11+, stdlib-only core, `rich` for the terminal UI, `pytest` + `ruff`. 100% local heuristics in v0.1 — no LLM, no network required.

## License

MIT (see `LICENSE`).
