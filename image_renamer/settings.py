"""Remembers where the operator last browsed to, across app restarts.
No GUI imports."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .profile import default_profiles_dir

LAST_FOLDER = "last_folder"
LAST_IMAGE_DIR = "last_image_dir"


def default_settings_path() -> Path:
    return default_profiles_dir().parent / "settings.json"


def load(path: str | Path | None = None) -> dict:
    """Never raises: a missing or corrupt file just means no remembered
    folders yet, which must not stop the app from starting."""
    path = Path(path) if path is not None else default_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(settings: dict, path: str | Path | None = None) -> None:
    """Best-effort: failing to remember a folder is not worth an error
    dialog in the middle of a batch."""
    path = Path(path) if path is not None else default_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


def nearest_existing_dir(folder: str | None) -> str | None:
    """The folder itself if it still exists, else its closest surviving
    parent -- e.g. after a batch folder was renamed or moved, the picker
    still opens near it rather than back at the default location."""
    if not folder:
        return None
    current = os.path.abspath(folder)
    while not os.path.isdir(current):
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return current
