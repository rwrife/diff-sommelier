# diff-sommelier 🍷

> *"This hunk has notes of off-by-one and a dangerous finish. I'd review it first."*

## 1. Pitch

`diff-sommelier` is a code-review **attention budget** tool. It reads a diff (yours, or the 2,000-line dump your AI agent just produced), scores every hunk by how **risky** and **surprising** it is, and tells you the optimal **order to read** it — so you spend your five minutes of real attention on the three hunks that can actually hurt you, and skim the rest. It's a *reviewer-side* triage tool, not another auto-committer.

## 2. Trend inspiration

The AI-coding-agent boom means humans are now drowning in machine-generated diffs they're expected to review and approve. Things I saw while researching (June 2026):

- **The "atomic commits for AI agents" wave** — Hunknote (<https://www.hunknote.com/>), VibeGit (<https://github.com/kklemon/vibegit>), git-surgeon (<https://raine.dev/blog/atomic-commits-for-ai-agents/>), groupit (<https://pypi.org/project/groupit/>), git-hunk (<https://github.com/wkentaro/git-hunk>), llm-git-commits (<https://pypi.org/project/llm-git-commits/>). Every one of these solves the **agent/author** side: *splitting* a messy tree into clean commits.
- **Enterprises burning AI budgets on agent output** — e.g. *"Uber Burns Its 2026 AI Budget In Four Months On Claude Code"* (<https://www.forbes.com/sites/janakirammsv/2026/05/17/uber-burns-its-2026-ai-budget-in-four-months-on-claude-code/>). More agent output → more review load on humans.
- **Product Hunt Weekly (2026-05-07)**: over half the top-20 launches were AI-agent infrastructure — *"VMs, observability, shared context boards"* (<https://www.shareuhack.com/en/posts/product-hunt-weekly-2026-05-07>). The ecosystem is racing to *produce* and *orchestrate* agent work; almost nothing helps the human *review* it efficiently.
- **Semantic diff primitives** like Sem (<https://aitoolly.com/ai-news/article/2026-06-07-sem-a-new-semantic-primitive-for-code-understanding-built-on-top-of-git>) and `git-semantic` show appetite for "diffs that understand structure," but they're about *understanding/search*, not *triage/prioritization*.

The gap: everyone optimizes the **author** side (write better commits). Nobody optimizes the **reviewer** side (read the dangerous parts first). That's diff-sommelier.

## 3. Why it's different

| Tool | What it does | Side |
|---|---|---|
| Hunknote / VibeGit / groupit / git-surgeon / git-hunk | Split a working tree into atomic commits | **Author** |
| llm-git-commits | Generate commit messages + stage hunks | **Author** |
| Sem / git-semantic | Semantic understanding & search of changes | Analysis |
| **diff-sommelier** | **Rank hunks by risk + surprise, emit a reading order & attention budget** | **Reviewer** |

Concretely, diff-sommelier does NOT split, commit, stage, or rewrite anything. It is **read-only**. It answers one question the others ignore: *"I have 7 minutes — what do I read, in what order, and what can I safely skim?"* It works on **any** diff (git, a `.patch` file, `gh pr diff`, stdin) — you don't have to adopt a new commit workflow. And the v0.1 scoring is **100% local, heuristic, no LLM required** (LLM is an optional enrichment later), so it's fast, free, and offline.

## 4. MVP scope (v0.1)

The smallest useful thing:

- `diff-sommelier` reads a unified diff from **stdin**, a **file** (`-f changes.patch`), or **git** (`--staged` / `--range main..HEAD`).
- Parse the diff into **files → hunks** with line ranges and +/- counts.
- Score each hunk with a transparent **heuristic risk model** (see Architecture). Every point is explainable.
- Output a ranked **"tasting menu"**: hunks ordered most-risky-first, each with a score, a one-line "why" (the rules that fired), and its file:line location.
- An **attention budget** mode: `--budget 5m` (or `--budget 10hunks`) draws a cut line — "review above the line, skim below."
- Pretty terminal output (color, score bars) + `--json` for machines/agents.
- Exit non-zero if any hunk exceeds `--fail-over <score>` (so CI can flag "this PR has a scary hunk no one may have read").

That's it. No staging, no network, no LLM.

## 5. Tech stack

Boring, fast, and zero-friction to install:

- **Python 3.11+** — ubiquitous, great stdlib, trivial CLI distribution. Diff/text munging is its home turf.
- **stdlib only for the core** (`argparse`, `re`, `subprocess`, `dataclasses`, `json`) — the unified-diff parser and heuristics need no deps, keeping install instant and supply-chain tiny.
- **`rich`** (single, well-trusted dep) for the terminal "tasting menu" UI; degrade gracefully to plain text if absent.
- **`pytest`** for tests; **`ruff`** for lint/format.
- Packaged with **`pyproject.toml`**, runnable via `pipx install` or `python -m diff_sommelier`.

Why not Rust/Go? v0.1 is text-wrangling + heuristics; Python ships an MVP in hours and stays trivially hackable for the backlog. Performance is a non-issue at human-diff sizes.

## 6. Architecture

```
stdin / file / git  ──▶  source.py        (acquire raw unified diff)
                          │
                          ▼
                       parser.py          (diff → File[] → Hunk[]; stable hunk IDs)
                          │
                          ▼
                       rules/             (each rule: Hunk -> [Signal(points, reason)])
                          │   ├─ size.py        (big hunks / churn)
                          │   ├─ surface.py     (touches auth/crypto/migrations/CI/deps/Dockerfile)
                          │   ├─ danger.py      (deletes, regex of eval/exec/secrets, perms, SQL)
                          │   ├─ control.py     (changed conditionals, error handling, off-by-one bait)
                          │   └─ surprise.py    (mixes unrelated files, touches generated/lockfiles)
                          ▼
                       scorer.py          (sum signals → 0–100, attach explanations)
                          │
                          ▼
                       budget.py          (apply --budget cut line)
                          │
                          ▼
                    render/  text.py | rich.py | json.py
```

Key modules:
- **parser.py** — the one piece that must be correct; a small, well-tested unified-diff parser producing stable content-hash hunk IDs.
- **rules/** — pluggable scoring rules; each returns weighted `Signal`s with human-readable reasons. This is the extension surface for the entire backlog.
- **scorer.py** — combines signals, normalizes to 0–100, keeps the "why."
- **render/** — swappable presenters (human, JSON, later: Markdown/HTML).

## 7. Milestones

1. **M1 — Scaffold + hello-world.** `pyproject.toml`, package skeleton, `diff-sommelier --version`, CI that runs ruff + pytest, one placeholder test. Reads stdin and echoes a parsed file/hunk count.
2. **M2 — Diff parser.** Robust unified-diff parser (multi-file, multi-hunk, renames, binary markers, new/deleted files) → typed `File`/`Hunk` model with stable IDs. Heavily tested with fixture diffs.
3. **M3 — Heuristic scoring engine.** The `rules/` package + `scorer.py`. Ship size/surface/danger rules first, each with reasons. `--json` output of scored hunks.
4. **M4 — The tasting menu (human output).** `rich` ranked view: score bars, file:line, "why," sorted most-risky-first. Graceful plain-text fallback.
5. **M5 — Attention budget + CI gate.** `--budget 5m|Nhunks` cut line, time model (≈ reading speed per changed line), and `--fail-over <score>` non-zero exit for CI.
6. **M6 — Git & PR ergonomics.** `--staged`, `--range A..B`, `gh pr diff` ingestion, config file (`.sommelier.toml`) for custom rule weights/paths, and a polished README with screenshots.

## 8. Backlog / future features (v0.2+)

1. **LLM enrichment (optional)** — for top-N hunks only, ask a model "what could break here?" to augment heuristic reasons. Strictly opt-in; heuristics remain the default.
2. **`--blast-radius`** — cross-reference changed symbols against the rest of the repo to flag "this small hunk is imported in 40 places."
3. **CODEOWNERS awareness** — boost hunks in files owned by someone other than the PR author / outside the reviewer's expertise.
4. **Historical hotspot weighting** — mine `git log` to score churny/bug-prone files higher (Michael Feathers-style hotspots).
5. **GitHub Action** — post a "review this order" checklist comment on every PR; collapse the skim-safe hunks.
6. **Test-coverage cross-check** — flag risky hunks in files with no nearby test changes.
7. **Reviewer profiles** — `--as backend` vs `--as frontend` reweights surfaces toward your blind spots.
8. **Secret/PII radar** — promote `danger.py` into a sharper credential/PII detector with allowlist.
9. **HTML/Markdown report** — shareable "review menu" artifact for async review.
10. **Language-aware parsing** — tree-sitter to know a hunk changed a function signature vs a comment.
11. **"Surprise vs the PR's stated intent"** — diff the actual changes against the PR title/description; flag hunks that don't match the story.
12. **Editor integrations** — VS Code / Neovim "jump to next risky hunk" using the JSON output.

## 9. Out of scope

- **Splitting, staging, committing, or rewriting** anything — that lane is crowded (Hunknote/VibeGit/git-surgeon/etc.) and diff-sommelier is deliberately **read-only**.
- **Being a linter or SAST tool** — we rank *attention*, we don't claim to find every bug or block merges on rules.
- **Mandatory LLM/cloud calls** — the core stays local, free, and offline; AI is opt-in enrichment only.
- **A hosted web app / dashboard / accounts** — it's a CLI (plus an optional GitHub Action later).
- **Non-unified diff formats** (e.g. raw AST diffs) in v0.1.
