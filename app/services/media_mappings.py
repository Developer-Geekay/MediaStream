"""
Runtime folder mapping storage.
Mappings are added/removed via the web UI and persisted in config/media_mappings.json.
Files are served in-place — nothing is ever copied.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

MAPPINGS_FILE = Path("./config/media_mappings.json")
_lock = threading.Lock()


def _load() -> list[dict]:
    """Load from disk without acquiring the lock (caller must hold it)."""
    if not MAPPINGS_FILE.exists():
        return []
    try:
        with open(MAPPINGS_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return [
        {"name": m["name"], "path": Path(m["path"]).resolve()}
        for m in raw
        if isinstance(m, dict) and m.get("name") and m.get("path")
    ]


def _save(mappings: list[dict]) -> None:
    MAPPINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = [{"name": m["name"], "path": str(m["path"])} for m in mappings]
    with open(MAPPINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_mappings() -> list[dict]:
    with _lock:
        return _load()


def add_mapping(path_str: str, name: str | None = None) -> dict:
    """
    Add a new folder mapping.
    name is optional — uses the folder's own name if omitted.
    Raises ValueError on duplicate name/path or non-existent directory.
    """
    p = Path(path_str).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError(f"Path does not exist or is not a directory: {path_str}")

    effective_name = (name or "").strip() or p.name

    with _lock:
        mappings = _load()
        for m in mappings:
            if m["name"] == effective_name:
                raise ValueError(f"A folder named '{effective_name}' is already mapped")
            if str(m["path"]) == str(p):
                raise ValueError(f"'{p}' is already mapped as '{m['name']}'")
        new = {"name": effective_name, "path": p}
        mappings.append(new)
        _save(mappings)

    return {"name": effective_name, "path": str(p)}


def remove_mapping(name: str) -> bool:
    """Remove a mapping by name. Returns True if removed, False if not found."""
    with _lock:
        mappings = _load()
        new_list = [m for m in mappings if m["name"] != name]
        if len(new_list) == len(mappings):
            return False
        _save(new_list)
    return True
