"""Helpers for the real-store guard (tests/conftest.py): snapshot a store and list what changed."""

from __future__ import annotations

from pathlib import Path

REAL_STORE = Path.home() / ".pronunciation_lab" / "reader"


def store_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root` with its size and modification time (empty when the store does not exist)."""
    if not root.exists():
        return {}
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def store_changes(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    changed = [f"changed: {k}" for k in sorted(set(before) & set(after)) if before[k] != after[k]]
    return changed + [f"created: {k}" for k in sorted(set(after) - set(before))] + \
        [f"removed: {k}" for k in sorted(set(before) - set(after))]
