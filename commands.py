# SPDX-License-Identifier: MIT
"""
FreeCAD command classes registered by InitGui.py.

Each command is a small adapter: toolbar / menu click → chat panel method.
"""

from __future__ import annotations

import os

from core.paths import ICON_APPROVE, ICON_CHAT, ICON_MAIN, ICON_NEW, ICON_SCREENSHOT
from core import log


def _icon(path) -> str:
    return str(path) if path and os.path.isfile(str(path)) else str(ICON_MAIN)


class _Command:
    """Shared helpers. FreeCAD looks up GetResources / Activated / IsActive."""

    def IsActive(self) -> bool:  # noqa: N802
        return True


class CommandOpenChat(_Command):
    def GetResources(self) -> dict:  # noqa: N802
        return {
            "Pixmap": _icon(ICON_CHAT),
            "MenuText": "Open Grok Chat",
            "ToolTip": "Open the Grok CAD Agent dock (chat, tools, vision).",
            "Accel": "G, C",
        }

    def Activated(self) -> None:  # noqa: N802
        from ui.GrokChatPanel import ensure_panel

        try:
            ensure_panel()
        except Exception as exc:  # noqa: BLE001
            log.error(f"Could not open Grok chat: {exc}", exc)


class CommandScreenshot(_Command):
    def GetResources(self) -> dict:  # noqa: N802
        return {
            "Pixmap": _icon(ICON_SCREENSHOT),
            "MenuText": "Take Screenshot for Grok",
            "ToolTip": "Capture isometric / front / top / right views and queue them for the next message.",
            "Accel": "G, S",
        }

    def Activated(self) -> None:  # noqa: N802
        from ui.GrokChatPanel import ensure_panel

        try:
            panel = ensure_panel()
            panel.take_screenshot_for_grok()
        except Exception as exc:  # noqa: BLE001
            log.error(f"Screenshot command failed: {exc}", exc)


class CommandApprove(_Command):
    def GetResources(self) -> dict:  # noqa: N802
        return {
            "Pixmap": _icon(ICON_APPROVE),
            "MenuText": "Approve Pending Action",
            "ToolTip": "Approve every tool call currently waiting in the chat panel.",
            "Accel": "G, A",
        }

    def Activated(self) -> None:  # noqa: N802
        from ui.GrokChatPanel import get_panel, ensure_panel

        panel = get_panel() or ensure_panel()
        panel.approve_pending()

    def IsActive(self) -> bool:  # noqa: N802
        from ui.GrokChatPanel import get_panel

        panel = get_panel()
        return bool(panel is not None and getattr(panel, "_pending", None))


class CommandNewSession(_Command):
    def GetResources(self) -> dict:  # noqa: N802
        return {
            "Pixmap": _icon(ICON_NEW),
            "MenuText": "New Design Session",
            "ToolTip": "Clear the conversation and start a fresh Grok session.",
        }

    def Activated(self) -> None:  # noqa: N802
        from ui.GrokChatPanel import ensure_panel

        ensure_panel().new_session()


class CommandPreferences(_Command):
    def GetResources(self) -> dict:  # noqa: N802
        return {
            "Pixmap": _icon(ICON_MAIN),
            "MenuText": "Grok CAD Preferences…",
            "ToolTip": "Open Edit → Preferences → Grok CAD Agent.",
        }

    def Activated(self) -> None:  # noqa: N802
        try:
            import FreeCADGui as Gui  # type: ignore

            Gui.showPreferences("Grok CAD Agent")
        except Exception:
            try:
                import FreeCADGui as Gui  # type: ignore

                Gui.showPreferences()
            except Exception as exc:  # noqa: BLE001
                log.error(f"Could not open preferences: {exc}", exc)


COMMAND_TABLE = {
    "GrokCAD_OpenChat": CommandOpenChat,
    "GrokCAD_Screenshot": CommandScreenshot,
    "GrokCAD_Approve": CommandApprove,
    "GrokCAD_NewSession": CommandNewSession,
    "GrokCAD_Preferences": CommandPreferences,
}

TOOLBAR_COMMANDS = [
    "GrokCAD_OpenChat",
    "GrokCAD_Screenshot",
    "GrokCAD_Approve",
    "GrokCAD_NewSession",
]

MENU_COMMANDS = TOOLBAR_COMMANDS + ["GrokCAD_Preferences"]


def register_commands() -> None:
    import FreeCADGui as Gui  # type: ignore

    for name, cls in COMMAND_TABLE.items():
        try:
            Gui.addCommand(name, cls())
        except Exception as exc:  # noqa: BLE001
            # Re-registering on workbench reload is harmless.
            log.warn(f"addCommand({name}) : {exc}")
