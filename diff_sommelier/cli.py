"""Command-line entry point for diff-sommelier.

It reads a unified diff from **stdin** and presents it as a ranked
**tasting menu** — hunks ordered most-risky-first, each with a risk tier
(savor / sip / gulp), a 0-100 score, a score bar, its ``file:line``, and the
one-line *why* (the rules that fired). Output modes:

* default — the human tasting menu (colour via :mod:`rich` when stdout is a
  terminal; deterministic plain text otherwise or with ``--no-color``);
* ``--json`` — the scored, explained hunks as a JSON array (the machine
  contract for agents, editors, and the budget/CI tooling);
* ``--sarif`` — the ranked hunks as a SARIF 2.1.0 log, for upload via
  ``upload-sarif`` so the risky hunks appear as inline code-scanning
  annotations (tier drives the SARIF level);
* ``--context-budget 6000tok|8hunks`` — a token-bounded, paste-ready review
  *bundle* of only the highest-risk hunks (most-dangerous-first) for an AI
  reviewer with a context limit (the machine-side sibling of ``--budget``).

``--budget 5m|90s|10hunks`` draws a cut line in the menu (review above, skim
below), and ``--fail-over <score>`` makes the process exit non-zero when any
hunk meets or exceeds the threshold, so CI can flag a scary unreviewed hunk.
``--blast-radius`` additionally cross-references changed symbols against the
rest of the repo, so a tiny edit to a widely-used name floats up the order.
``--hotspots`` mines ``git log`` so hunks in historically bug-prone files
(high churn, often fixed) float up too.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from diff_sommelier import __version__
from diff_sommelier import blast_radius as _blast_radius
from diff_sommelier import hotspots as _hotspots
from diff_sommelier import owners as _owners
from diff_sommelier.budget import (
    BudgetError,
    BudgetResult,
    apply_budget,
    fail_over,
    parse_budget,
)
from diff_sommelier.config import Config, ConfigError, load_config
from diff_sommelier.enrich import DEFAULT_TOP_N, EnrichmentError
from diff_sommelier.parser import parse_diff
from diff_sommelier.render import render_human, render_json, render_markdown, render_sarif
from diff_sommelier.render.bundle import ContextBudgetError, parse_context_budget
from diff_sommelier.scorer import ScoredHunk, score_diff
from diff_sommelier.source import SourceError, read_git

PROG = "diff-sommelier"

# Exit code returned when --fail-over trips (a hunk met/exceeded the threshold).
# Distinct from 2, which argparse uses for usage errors.
EXIT_FAIL_OVER = 1


@dataclass(frozen=True)
class DiffCounts:
    """Summary of a unified diff: how many files and hunks."""

    files: int
    hunks: int


def count_diff(lines: Iterable[str]) -> DiffCounts:
    """Count files and hunks in a unified diff using the real M2 parser.

    Kept as a thin convenience wrapper so callers (and tests) can get the
    file/hunk tally consistent with the typed model in
    :mod:`diff_sommelier.parser`.
    """
    diff = parse_diff(lines)
    return DiffCounts(files=len(diff.files), hunks=len(diff.hunks))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Triage your code-review attention: rank diff hunks by risk + "
            "surprise and tell you what to read first. Reads a unified diff "
            "from stdin and prints a ranked 'tasting menu' (or scored hunks "
            "as JSON with --json)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )

    # Diff source. Default is stdin (pipe `git diff` or `gh pr diff` in). These
    # two convenience flags shell out to git instead and are mutually exclusive
    # with each other; when either is given, stdin is not read.
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--staged",
        action="store_true",
        help="diff the git index (`git diff --cached`) instead of reading stdin",
    )
    source.add_argument(
        "--range",
        dest="range_spec",
        metavar="A..B",
        default=None,
        help=(
            "diff a git range (`git diff A..B`) instead of reading stdin, e.g. --range main..HEAD"
        ),
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help=(
            "emit scored, explained hunks as a JSON array (most risky first) "
            "instead of the human tasting menu"
        ),
    )
    output.add_argument(
        "--markdown",
        action="store_true",
        help=(
            "emit a GitHub-flavoured Markdown menu sized for a PR comment: a "
            "reading-order checklist with the skim-safe hunks in a collapsed "
            "section. Used by the GitHub Action. Honours --fail-over for the "
            "CI-gate note; pair with --title to name the PR."
        ),
    )
    output.add_argument(
        "--sarif",
        action="store_true",
        help=(
            "emit a SARIF 2.1.0 log (JSON) of the ranked hunks instead of the "
            "human menu, so they can be uploaded with `upload-sarif` and show "
            "up as inline code-scanning annotations in the PR 'Files changed' "
            "view and the Security tab. Risk tier drives the SARIF level "
            "(gulp=error, sip=warning, savor=note). Honours --fail-over and "
            "--title (recorded in the log's properties)."
        ),
    )
    output.add_argument(
        "--context-budget",
        metavar="SPEC",
        dest="context_budget",
        default=None,
        help=(
            "emit a token-bounded, paste-ready review BUNDLE of only the "
            "highest-risk hunks (most-dangerous-first) for an AI reviewer, "
            "instead of the human menu. SPEC is an approximate token cap "
            "('6000tok') or a hunk count ('8hunks', or a bare integer). "
            "Selection stops at the budget and a trailer reports how many "
            "lower-risk hunks were omitted. Pipe it to your reviewer; no "
            "network/LLM call is made. Pair with --title to include the "
            "stated intent in the preamble."
        ),
    )
    parser.add_argument(
        "--title",
        metavar="TEXT",
        default=None,
        help=(
            "override the heading of the --markdown menu (e.g. the PR title); "
            "also recorded in the --sarif log's run properties"
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="force plain-text output (no colour), even on a terminal",
    )
    parser.add_argument(
        "--budget",
        metavar="SPEC",
        default=None,
        help=(
            "draw a cut line in the menu: review the hunks above it, skim "
            "below. SPEC is a time ('5m', '90s', '1m30s') or a count "
            "('10hunks', or a bare integer). Ignored with --json."
        ),
    )
    parser.add_argument(
        "--fail-over",
        metavar="SCORE",
        type=int,
        default=None,
        help=(
            "exit non-zero if any hunk's risk score is >= SCORE (0-100), so CI "
            "can flag a scary hunk. Combine with --json in a pipeline."
        ),
    )
    parser.add_argument(
        "--blast-radius",
        action="store_true",
        help=(
            "cross-reference changed symbols against the rest of the repo and "
            "flag small hunks that touch widely-used names. Scans the working "
            "tree (git-tracked files when available); no-ops outside a repo. "
            "Opt-in and fully local."
        ),
    )
    parser.add_argument(
        "--hotspots",
        action="store_true",
        help=(
            "boost hunks in historically bug-prone files by mining `git log` "
            "for per-file churn and fix frequency (Feathers-style hotspots). "
            "No-ops outside a git repo. Opt-in and fully local."
        ),
    )
    parser.add_argument(
        "--owners",
        action="store_true",
        help=(
            "boost hunks in files owned by someone other than the PR author "
            "(or with no CODEOWNERS entry at all) by reading the repo's "
            "CODEOWNERS. Requires --author. No-ops without a CODEOWNERS file "
            "or a resolvable author. Opt-in and fully local."
        ),
    )
    parser.add_argument(
        "--author",
        metavar="LOGIN",
        default=None,
        help=(
            "the PR author's CODEOWNERS handle (e.g. @octocat or a team like "
            "@org/team), used by --owners to skip files the author already "
            "owns. Compare case-insensitively."
        ),
    )
    parser.add_argument(
        "--explain-llm",
        action="store_true",
        help=(
            "opt-in: after scoring, send only the top-N riskiest hunks to a "
            "model and fold its 'what could break here?' notes into the reasons "
            "(clearly labelled 'model:', and never changing the score). Off by "
            "default; the core stays 100%% local. Requires a backend via the "
            "SOMMELIER_LLM_BACKEND env var (use 'echo' for a local demo)."
        ),
    )
    parser.add_argument(
        "--explain-llm-top",
        metavar="N",
        dest="explain_llm_top",
        type=int,
        default=DEFAULT_TOP_N,
        help=(
            "how many top-ranked hunks to send when --explain-llm is set "
            f"(default {DEFAULT_TOP_N}). Only these hunks are ever sent; the "
            "rest of the diff never leaves your machine."
        ),
    )

    # Config (.sommelier.toml) discovery controls.
    cfg = parser.add_mutually_exclusive_group()
    cfg.add_argument(
        "--config",
        metavar="PATH",
        dest="config_path",
        default=None,
        help=(
            "use this .sommelier.toml (rule weights + extra surface paths) "
            "instead of discovering one by walking up from the cwd"
        ),
    )
    cfg.add_argument(
        "--no-config",
        action="store_true",
        help="ignore any .sommelier.toml and use the built-in defaults",
    )
    return parser


def _resolve_budget(
    scored: Sequence[ScoredHunk],
    budget_spec: str | None,
) -> BudgetResult | None:
    """Parse the --budget spec and compute the cut over ``scored``.

    Returns ``None`` when no budget was requested. Raises
    :class:`~diff_sommelier.budget.BudgetError` for an invalid spec so the CLI
    can report it cleanly.
    """
    if budget_spec is None:
        return None
    return apply_budget(scored, parse_budget(budget_spec))


def _acquire_diff(args: argparse.Namespace) -> str:
    """Get the raw unified diff text from the selected source.

    stdin is the default (and how ``gh pr diff`` is piped in). ``--staged`` and
    ``--range`` shell out to git instead. Raises
    :class:`~diff_sommelier.source.SourceError` on a git problem so :func:`main`
    can report it on stderr with a non-zero exit.
    """
    if args.staged or args.range_spec is not None:
        return read_git(staged=args.staged, range_spec=args.range_spec)
    return sys.stdin.read()


def _load_cli_config(args: argparse.Namespace) -> Config:
    """Resolve the .sommelier.toml for this invocation (honouring the flags)."""
    explicit = Path(args.config_path) if args.config_path else None
    return load_config(explicit=explicit, enabled=not args.no_config)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    using_git = args.staged or args.range_spec is not None
    if not using_git and sys.stdin.isatty():
        # No piped diff and no git source; nothing to do. Point at --help.
        parser.print_usage()
        print(
            f"{PROG}: no diff on stdin. Pipe a unified diff (e.g. `git diff | {PROG}`), "
            f"or use --staged / --range A..B.",
            file=sys.stderr,
        )
        return 0

    try:
        config = _load_cli_config(args)
    except ConfigError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    try:
        raw = _acquire_diff(args)
    except SourceError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    # Read once so we can both render and run the --fail-over gate over the same
    # scored diff without re-acquiring the source.
    diff = parse_diff(raw.splitlines(keepends=True))

    # Assemble the active rule list: the config-tuned built-ins, plus the opt-in
    # blast-radius rule when --blast-radius is set and there is a tree to scan.
    # build_index returns None outside a repo / with nothing to scan, so this
    # cleanly no-ops rather than erroring.
    rules = config.rules()
    if args.blast_radius:
        index = _blast_radius.build_index()
        rules = _blast_radius.append_rule(rules, index, weight=config.apply_weight)
    if args.hotspots:
        hotspot_index = _hotspots.build_index()
        rules = _hotspots.append_rule(rules, hotspot_index, weight=config.apply_weight)
    if args.owners:
        owners_index = _owners.build_index()
        rules = _owners.append_rule(
            rules, owners_index, args.author, weight=config.apply_weight
        )

    scored = score_diff(diff, rules=rules)

    # Opt-in LLM enrichment (issue #7): after the heuristics have ranked the
    # hunks, augment only the top-N riskiest with model notes. Imported lazily so
    # the default, offline path never even loads the module. Notes are zero-point
    # and clearly labelled, so they explain without moving the score or the
    # ranking; a misconfigured/failed backend is reported cleanly (exit 2).
    if args.explain_llm:
        from diff_sommelier.enrich import enrich

        try:
            scored = enrich(scored, top_n=args.explain_llm_top)
        except EnrichmentError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2

    # Colour only when asked for (default) AND stdout is a real terminal, so
    # piping the menu into a file or pager yields clean plain text.
    color = not args.no_color and sys.stdout.isatty()

    if args.json:
        print(render_json(scored))
    elif args.markdown:
        print(render_markdown(scored, title=args.title, fail_over=args.fail_over))
    elif args.sarif:
        print(render_sarif(scored, title=args.title, fail_over=args.fail_over))
    elif args.context_budget is not None:
        try:
            ctx_budget = parse_context_budget(args.context_budget)
        except ContextBudgetError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
        from diff_sommelier.render import render_bundle

        print(render_bundle(scored, budget=ctx_budget, title=args.title))
    else:
        try:
            budget = _resolve_budget(scored, args.budget)
        except BudgetError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
        print(render_human(scored, color=color, budget=budget))

    # CI gate: a non-None worst score means a hunk met/exceeded the threshold.
    if args.fail_over is not None:
        worst = fail_over(scored, args.fail_over)
        if worst is not None:
            print(
                f"{PROG}: fail-over tripped — a hunk scored {worst} (>= {args.fail_over}).",
                file=sys.stderr,
            )
            return EXIT_FAIL_OVER
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
