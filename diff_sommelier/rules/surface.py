"""Surface rule (M3).

Some files are dangerous *by location*, independent of what the change says.
A one-line edit to an auth check, a database migration, a CI workflow, a
Dockerfile, or a dependency lockfile deserves more attention than a one-line
edit to a README — because the blast radius of getting it wrong is large.

This rule classifies a hunk by its **file path** (and, for lockfiles, name) and
emits a signal for each sensitive surface it touches. Matching is done on the
normalized path with a set of conservative, well-known patterns so that a clean
source edit scores nothing here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from diff_sommelier.parser import File, Hunk
from diff_sommelier.rules import Signal

RULE = "surface"

# Each entry: (compiled path pattern, points, reason). Patterns are matched
# case-insensitively against the forward-slashed path. They are intentionally
# specific; a generic ".py" file matches nothing here.
#
# Points reflect "how bad is a silent mistake here":
#   auth/crypto and migrations are the scariest (irreversible / security),
#   CI, infra, and deps are next (supply chain / pipeline), config is mild.
_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(r"(^|/)(auth|authn|authz|login|session|password|oauth|jwt)([./_-]|$)", re.I),
        14,
        "touches authentication/session code",
    ),
    (
        re.compile(r"(^|/)(crypto|cipher|encrypt|secret|signing|keystore|tls|ssl)([./_-]|$)", re.I),
        14,
        "touches cryptography/secrets code",
    ),
    (
        re.compile(r"(^|/)(migrations?|alembic|schema)([./_-]|/|$)", re.I),
        13,
        "touches a database migration/schema",
    ),
    (
        re.compile(r"(^|/)\.github/workflows/", re.I),
        10,
        "touches CI workflow",
    ),
    (
        re.compile(r"(^|/)(dockerfile|docker-compose|\.dockerignore)", re.I),
        9,
        "touches container/build config",
    ),
    (
        re.compile(
            r"(^|/)(\.gitlab-ci\.yml|\.circleci/|jenkinsfile|\.travis\.yml|azure-pipelines)", re.I
        ),
        10,
        "touches CI/CD pipeline config",
    ),
    (
        re.compile(
            r"(^|/)(package\.json|pyproject\.toml|requirements[^/]*\.txt|setup\.(py|cfg)|"
            r"go\.mod|cargo\.toml|gemfile|build\.gradle|pom\.xml)$",
            re.I,
        ),
        8,
        "changes declared dependencies/build",
    ),
    (
        re.compile(
            r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|"
            r"cargo\.lock|gemfile\.lock|go\.sum|composer\.lock)$",
            re.I,
        ),
        6,
        "changes a dependency lockfile",
    ),
    (
        re.compile(r"(^|/)(\.env|\.npmrc|\.pypirc)([./_-]|$)", re.I),
        12,
        "touches environment/credentials config",
    ),
)


def score(hunk: Hunk, file: File) -> Iterator[Signal]:
    """Yield one signal per sensitive surface the hunk's path matches."""
    path = hunk.file_path.replace("\\", "/")
    for pattern, points, reason in _PATTERNS:
        if pattern.search(path):
            yield Signal(rule=RULE, points=points, reason=reason)
