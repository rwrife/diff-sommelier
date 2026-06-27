"""Unit tests for the surface rule (M3)."""

from __future__ import annotations

from diff_sommelier.parser import ChangeType, File, Hunk
from diff_sommelier.rules import surface


def hunk_for(path: str) -> Hunk:
    """A trivial hunk whose only interesting property is its file path."""
    return Hunk(
        file_path=path,
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=1,
        heading="",
        body="+x",
        added=1,
        removed=0,
    )


FILE = File(path="x", change_type=ChangeType.MODIFIED)


def reasons(path: str) -> list[str]:
    return [s.reason for s in surface.score(hunk_for(path), FILE)]


def test_plain_source_file_is_not_sensitive() -> None:
    assert reasons("src/widgets/button.py") == []
    assert reasons("docs/guide.md") == []


def test_auth_path_flagged() -> None:
    assert any("authentication" in r for r in reasons("src/auth/session.py"))
    assert any("authentication" in r for r in reasons("app/login_view.py"))


def test_crypto_path_flagged() -> None:
    assert any("cryptography" in r for r in reasons("lib/crypto/aes.py"))
    assert any("cryptography" in r for r in reasons("server/tls_config.go"))


def test_migration_flagged() -> None:
    assert any("migration" in r for r in reasons("db/migrations/0007_add_users.py"))
    assert any("migration" in r for r in reasons("alembic/versions/abc.py"))


def test_ci_workflow_flagged() -> None:
    assert any("CI workflow" in r for r in reasons(".github/workflows/ci.yml"))


def test_dockerfile_flagged() -> None:
    assert any("container/build" in r for r in reasons("Dockerfile"))
    assert any("container/build" in r for r in reasons("deploy/docker-compose.yml"))


def test_dependency_manifest_flagged() -> None:
    assert any("declared dependencies" in r for r in reasons("pyproject.toml"))
    assert any("declared dependencies" in r for r in reasons("frontend/package.json"))


def test_lockfile_flagged_lower_than_manifest() -> None:
    manifest = next(iter(surface.score(hunk_for("package.json"), FILE)))
    lock = next(iter(surface.score(hunk_for("package-lock.json"), FILE)))
    assert "lockfile" in lock.reason
    assert lock.points < manifest.points


def test_env_file_flagged() -> None:
    assert any("environment/credentials" in r for r in reasons(".env.production"))


def test_windows_style_path_separators_normalized() -> None:
    assert any("authentication" in r for r in reasons("src\\auth\\login.py"))
