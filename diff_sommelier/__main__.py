"""Allow running the package via ``python -m diff_sommelier``."""

from __future__ import annotations

from diff_sommelier.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
