"""Safe source-view links shared by the portable report presenters."""

from urllib.parse import quote, urlsplit


def validate_repo_url(base: str) -> str:
    """Require an absolute HTTP(S) source-view base, including its revision."""
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(char) <= 32 for char in base)
    ):
        raise ValueError(
            "--repo-url must be an absolute HTTP(S) source base without query/fragment"
        )
    return base.rstrip("/")


def source_link(base: str, path: str, line: int) -> str:
    """Append a quoted repository path and a one-based line anchor."""
    base = validate_repo_url(base)
    # Encode dot-only segments too, so filenames cannot navigate above the base.
    path = "/".join(
        "%2E" * len(part) if part in {".", ".."} else quote(part, safe="")
        for part in path.split("/")
    )
    return f"{quote(base, safe=':/%')}/{path}#L{max(1, line)}"
