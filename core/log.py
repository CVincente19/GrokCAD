# SPDX-License-Identifier: MIT
"""User-visible logging: FreeCAD Report View + optional in-memory ring buffer."""

from __future__ import annotations

import traceback
from collections import deque
from typing import Callable, Deque, List, Optional

_PREFIX = "[GrokCAD] "
_RING: Deque[str] = deque(maxlen=500)
_LISTENERS: List[Callable[[str, str], None]] = []


def add_listener(fn: Callable[[str, str], None]) -> None:
    """Subscribe to (level, message) events. Used by the chat status strip."""
    if fn not in _LISTENERS:
        _LISTENERS.append(fn)


def remove_listener(fn: Callable[[str, str], None]) -> None:
    try:
        _LISTENERS.remove(fn)
    except ValueError:
        pass


def _emit(level: str, message: str) -> None:
    text = message if message.endswith("\n") else message + "\n"
    _RING.append(f"{level}: {text.rstrip()}")
    for fn in list(_LISTENERS):
        try:
            fn(level, text.rstrip())
        except Exception:  # noqa: BLE001
            pass
    try:
        import FreeCAD  # type: ignore

        cons = FreeCAD.Console
        line = _PREFIX + text
        if level == "error":
            cons.PrintError(line)
        elif level == "warn":
            cons.PrintWarning(line)
        else:
            cons.PrintMessage(line)
    except Exception:  # noqa: BLE001
        # Running outside FreeCAD (installer syntax check, unit tests).
        import sys

        stream = sys.stderr if level in {"error", "warn"} else sys.stdout
        stream.write(_PREFIX + text)


def info(message: str) -> None:
    _emit("info", message)


def warn(message: str) -> None:
    _emit("warn", message)


def error(message: str, exc: Optional[BaseException] = None) -> None:
    if exc is not None:
        message = f"{message}\n{traceback.format_exc()}"
    _emit("error", message)


def history() -> List[str]:
    return list(_RING)
