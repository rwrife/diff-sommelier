<!-- diff-sommelier:review-menu -->
## Example review

**3 hunks** across **3 files** · top risk **87**

### Read these first

_In recommended reading order, most-risky-first._

| | # | Tier | Score | Location | Why |
|---|---:|---|---:|---|---|
| [ ] | 1 | 🔴 gulp | 87 <code>[#################   ]</code> | [<code>auth/login.py:1</code>](https://github.com/example/project/blob/abc123/auth/login.py#L1) | adds dynamic eval/exec (+16); touches authentication/session code (+14); changes a conditional (+4); adds/removes an early return/continue/break (x2) (+4) |

> **cut: review 1 above · skim 2 below · budget 1 of 3 hunks**

<details>
<summary>Below budget cut · 2 hunks</summary>

| | # | Tier | Score | Location | Why |
|---|---:|---|---:|---|---|
| [ ] | 2 | 🟡 sip | 41 <code>[########            ]</code> | [<code>.github/workflows/ci.yml:1</code>](https://github.com/example/project/blob/abc123/.github/workflows/ci.yml#L1) | touches CI workflow (+10) |
| [ ] | 3 | 🟢 savor | 0 <code>[                    ]</code> | [<code>README.md:1</code>](https://github.com/example/project/blob/abc123/README.md#L1) | _no notable signals — skim-safe_ |

</details>

<sub>Tiers: 🔴 gulp (read first, ≥60) · 🟡 sip (read, ≥25) · 🟢 savor (skim-safe, <25). Ranked by risk + surprise · [diff-sommelier](https://github.com/rwrife/diff-sommelier)</sub>
