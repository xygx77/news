"""Filesystem locations shared by the daily briefing pipeline."""

from __future__ import annotations

from pathlib import Path


def _find_upwards(start: Path, marker: str) -> Path | None:
    """Return the nearest ancestor containing ``marker``."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / marker).exists():
            return candidate
    return None


def project_root() -> Path:
    """Locate the project root from the cwd first, then from this module."""
    for start in (Path.cwd(), Path(__file__)):
        root = _find_upwards(start, "pyproject.toml")
        if root is not None:
            return root
    return Path.cwd()


def templates_dir() -> Path:
    """Directory holding the daily HTML templates."""
    return project_root() / "templates" / "daily"


def data_dir() -> Path:
    """Directory holding daily configuration and keyword files."""
    return project_root() / "data"
