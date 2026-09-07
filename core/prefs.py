# SPDX-License-Identifier: MIT
"""
Centralized GrokCAD preferences.

Values are stored in FreeCAD's parameter system:

    User parameter:BaseApp/Preferences/Mod/GrokCAD

The API key is never written to disk by this workbench except through that
parameter group (or read from the ``XAI_API_KEY`` / ``GROK_API_KEY``
environment variables). It is never logged and never embedded in exported
conversations.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from . import log

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/GrokCAD"

# Public model list shown in the UI. The client will probe the API and may
# add extra models discovered at runtime.
KNOWN_MODELS: List[str] = [
    "grok-4.6",
    "grok-4",
    "grok-4-fast",
    "grok-3",
    "grok-3-mini",
    "grok-2-latest",
    "grok-2-vision-latest",
]

DEFAULT_MODEL = "grok-4.6"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 8192
DEFAULT_SCREEN_W = 1600
DEFAULT_SCREEN_H = 1200

# Agent modes. Values are persisted as these exact strings.
MODE_PLAN_ONLY = "plan_only"
MODE_APPROVE_EVERY = "approve_every"
MODE_APPROVE_CODE = "approve_code"
MODE_FULL_AUTO = "full_auto"

MODE_LABELS = {
    MODE_PLAN_ONLY: "Plan only",
    MODE_APPROVE_EVERY: "Plan + approve every tool",
    MODE_APPROVE_CODE: "Auto-run safe tools, approve code",
    MODE_FULL_AUTO: "Full auto (expert)",
}

# Tools that never mutate the document (auto-run in MODE_APPROVE_CODE).
SAFE_TOOLS = frozenset(
    {
        "get_document_state",
        "inspect_object",
        "take_screenshot",
        "get_selection",
        "list_open_documents",
        "set_camera_view",
        "search_objects",
        "get_workbench_info",
    }
)


def _group() -> Any:
    try:
        import FreeCAD  # type: ignore

        return FreeCAD.ParamGet(PARAM_PATH)
    except Exception:  # noqa: BLE001
        return None


def get_str(name: str, default: str = "") -> str:
    grp = _group()
    if grp is None:
        return default
    try:
        val = grp.GetString(name, default)
        return default if val is None else str(val)
    except Exception:  # noqa: BLE001
        return default


def set_str(name: str, value: str) -> None:
    grp = _group()
    if grp is None:
        return
    grp.SetString(name, value if value is not None else "")


def get_int(name: str, default: int = 0) -> int:
    grp = _group()
    if grp is None:
        return default
    try:
        return int(grp.GetInt(name, int(default)))
    except Exception:  # noqa: BLE001
        return default


def set_int(name: str, value: int) -> None:
    grp = _group()
    if grp is None:
        return
    grp.SetInt(name, int(value))


def get_float(name: str, default: float = 0.0) -> float:
    grp = _group()
    if grp is None:
        return default
    try:
        # FreeCAD parameters have no float type; store as string.
        raw = grp.GetString(name, "")
        if raw == "":
            return default
        return float(raw)
    except Exception:  # noqa: BLE001
        return default


def set_float(name: str, value: float) -> None:
    grp = _group()
    if grp is None:
        return
    grp.SetString(name, repr(float(value)))


def get_bool(name: str, default: bool = False) -> bool:
    grp = _group()
    if grp is None:
        return default
    try:
        return bool(grp.GetBool(name, bool(default)))
    except Exception:  # noqa: BLE001
        return default


def set_bool(name: str, value: bool) -> None:
    grp = _group()
    if grp is None:
        return
    grp.SetBool(name, bool(value))


def get_api_key() -> str:
    """Return the xAI API key from prefs, then environment."""
    key = get_str("ApiKey", "").strip()
    if key:
        return key
    for env in ("XAI_API_KEY", "GROK_API_KEY"):
        val = os.environ.get(env, "").strip()
        if val:
            return val
    return ""


def set_api_key(key: str) -> None:
    set_str("ApiKey", (key or "").strip())


def get_base_url() -> str:
    url = get_str("BaseUrl", DEFAULT_BASE_URL).strip()
    return url or DEFAULT_BASE_URL


def get_model() -> str:
    model = get_str("Model", DEFAULT_MODEL).strip()
    return model or DEFAULT_MODEL


def get_temperature() -> float:
    t = get_float("Temperature", DEFAULT_TEMPERATURE)
    return max(0.0, min(2.0, t))


def get_max_tokens() -> int:
    n = get_int("MaxTokens", DEFAULT_MAX_TOKENS)
    return max(256, min(128000, n))


def get_agent_mode() -> str:
    mode = get_str("AgentMode", MODE_APPROVE_CODE).strip()
    if mode not in MODE_LABELS:
        return MODE_APPROVE_CODE
    return mode


def get_screenshot_size() -> tuple[int, int]:
    w = get_int("ScreenshotWidth", DEFAULT_SCREEN_W)
    h = get_int("ScreenshotHeight", DEFAULT_SCREEN_H)
    return max(320, min(4096, w)), max(240, min(4096, h))


def get_screenshot_background() -> str:
    bg = get_str("ScreenshotBackground", "Current").strip() or "Current"
    return bg


def get_auto_screenshot() -> bool:
    # Default OFF: auto-screenshots after every script exploded the
    # Sprite-plate session past 100k input tokens and burned the API budget.
    return get_bool("AutoScreenshotAfterCode", False)


def get_auto_screenshot_views() -> List[str]:
    raw = get_str("AutoScreenshotViews", "Isometric")
    views = [v.strip() for v in raw.split(",") if v.strip()]
    return views or ["Isometric"]


def get_max_tool_iterations() -> int:
    return max(1, min(40, get_int("MaxToolIterations", 16)))


def get_request_timeout() -> float:
    return max(15.0, min(600.0, get_float("RequestTimeout", 180.0)))


def get_extra_instructions() -> str:
    return get_str("ExtraInstructions", "")


def get_dock_side() -> str:
    side = get_str("DockSide", "right").strip().lower()
    return "left" if side == "left" else "right"


def ensure_defaults() -> None:
    """Write defaults for any missing keys so the Preferences page is populated."""
    grp = _group()
    if grp is None:
        return
    # Only set if the key is absent (empty string counts as present for strings).
    if grp.GetString("Model", "") == "" and grp.GetString("Model", "MISSING") == "MISSING":
        pass
    if not get_str("Model", ""):
        set_str("Model", DEFAULT_MODEL)
    if not get_str("BaseUrl", ""):
        set_str("BaseUrl", DEFAULT_BASE_URL)
    if get_int("MaxTokens", 0) == 0:
        set_int("MaxTokens", DEFAULT_MAX_TOKENS)
    if get_int("ScreenshotWidth", 0) == 0:
        set_int("ScreenshotWidth", DEFAULT_SCREEN_W)
    if get_int("ScreenshotHeight", 0) == 0:
        set_int("ScreenshotHeight", DEFAULT_SCREEN_H)
    if not get_str("AgentMode", ""):
        set_str("AgentMode", MODE_APPROVE_CODE)
    if not get_str("ScreenshotBackground", ""):
        set_str("ScreenshotBackground", "Current")
    if not get_str("AutoScreenshotViews", ""):
        set_str("AutoScreenshotViews", "Isometric")
    if get_int("MaxToolIterations", 0) == 0:
        set_int("MaxToolIterations", 16)
    # First-run flag for bools: we cannot distinguish unset from False easily
    # for AutoScreenshotAfterCode, so default True only when the group is new.
    if not get_bool("_Initialized", False):
        set_bool("AutoScreenshotAfterCode", False)
        set_bool("StreamResponses", True)
        set_bool("SendSelectionContext", True)
        set_bool("_Initialized", True)
        log.info("Wrote default preferences.")


def mask_secret(secret: str) -> str:
    if not secret:
        return "(not set)"
    if len(secret) <= 8:
        return "••••••••"
    return secret[:3] + "…" + secret[-4:]
