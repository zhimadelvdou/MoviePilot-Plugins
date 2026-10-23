"""Shared path validation for optional actress subfolders."""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
from pathlib import Path
import unicodedata


def safe_component(value: str) -> str | None:
    """Accept one display-name path component and reject path/control syntax."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 120 or value in (".", "..") or ".." in value:
        return None
    if any(char in value for char in ("/", "\\", "\x00")):
        return None
    if any(unicodedata.category(char) in ("Cc", "Cs") for char in value):
        return None
    if Path(value).name != value or os.path.isabs(value):
        return None
    return value


def child_directory(parent: Path, name: str) -> tuple[Path, dict[str, int] | None] | None:
    """Return a safe direct child and its identity; missing children are allowed."""
    component = safe_component(name)
    if component is None or parent.is_symlink():
        return None
    try:
        root = parent.resolve(strict=True)
        if not root.is_dir():
            return None
        child = root / component
        if child.is_symlink():
            return None
        if not child.exists():
            return child, None
        if not child.is_dir() or child.resolve(strict=True) != child:
            return None
        stat = child.stat(follow_symlinks=False)
        return child, {"device": stat.st_dev, "inode": stat.st_ino}
    except (OSError, RuntimeError, ValueError):
        return None


def child_directory_matches(parent: Path, name: str, identity: dict[str, int] | None,
                            *, allow_created: bool = False) -> bool:
    """Fail closed if the validated child was created, replaced, or redirected."""
    current = child_directory(parent, name)
    if allow_created and identity is None:
        return current is not None
    return current is not None and current[1] == identity


def actor_names(media) -> list[str]:
    """Return unique non-empty actor names from the projected SDK media value."""
    rows = getattr(media, "actors", None) or []
    result = []
    for row in rows:
        value = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
        name = safe_component(value) if isinstance(value, str) else None
        if name and name not in result:
            result.append(name)
    return result
