# SPDX-License-Identifier: MIT
"""Filesystem locations for the GrokCAD workbench."""

from __future__ import annotations

import os
from pathlib import Path

# .../GrokCAD/core/paths.py -> .../GrokCAD
WORKBENCH_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOURCES_DIR = WORKBENCH_DIR / "resources"
ICONS_DIR = RESOURCES_DIR / "icons"
STYLES_DIR = RESOURCES_DIR / "styles"
PROMPTS_DIR = WORKBENCH_DIR / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.txt"
CHAT_QSS_FILE = STYLES_DIR / "chat.qss"

ICON_MAIN = ICONS_DIR / "GrokCAD.svg"
ICON_CHAT = ICONS_DIR / "chat.svg"
ICON_SCREENSHOT = ICONS_DIR / "screenshot.svg"
ICON_APPROVE = ICONS_DIR / "approve.svg"
ICON_SEND = ICONS_DIR / "send.svg"
ICON_STOP = ICONS_DIR / "stop.svg"
ICON_NEW = ICONS_DIR / "new_session.svg"
ICON_EXPORT = ICONS_DIR / "export.svg"
ICON_CLEAR = ICONS_DIR / "clear.svg"

# Sessions / exports live under the user's FreeCAD user dir when available.
_DEFAULT_USER_DATA = Path.home() / ".local" / "share" / "FreeCAD" / "GrokCAD"


def user_data_dir() -> Path:
    """Return (and create) the writable per-user GrokCAD data directory."""
    try:
        import FreeCAD  # type: ignore

        # FreeCAD 1.0+: getUserAppDataDir() -> ~/.local/share/FreeCAD/
        base = Path(FreeCAD.getUserAppDataDir()) / "GrokCAD"
    except Exception:  # noqa: BLE001
        base = _DEFAULT_USER_DATA
    base.mkdir(parents=True, exist_ok=True)
    (base / "sessions").mkdir(exist_ok=True)
    (base / "screenshots").mkdir(exist_ok=True)
    (base / "exports").mkdir(exist_ok=True)
    return base


def screenshot_dir() -> Path:
    d = user_data_dir() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_dir() -> Path:
    d = user_data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def icon_path(name: str) -> str:
    """Return an absolute filesystem path for a named icon (with or without .svg)."""
    if not name.endswith(".svg") and not name.endswith(".png"):
        name = f"{name}.svg"
    p = ICONS_DIR / name
    return str(p)
