# SPDX-License-Identifier: MIT
"""
Qt / PySide compatibility layer for FreeCAD 1.0 and 1.1+.

FreeCAD vendors a ``PySide`` shim. Depending on the build this may be
PySide2 (Qt5) or PySide6 (Qt6). Import through this module everywhere
so the rest of GrokCAD stays version-agnostic.

On Fedora (dnf FreeCAD) this is always available inside the FreeCAD
process. Never import PySide at module import time from ``Init.py``.
"""

from __future__ import annotations

from typing import Any, Optional

_IMPORT_ERROR: Optional[BaseException] = None

QtCore: Any
QtGui: Any
QtWidgets: Any
Signal: Any
Slot: Any
QAction: Any

try:
    # Preferred: FreeCAD's shim (works for both Qt5 and Qt6 builds).
    from PySide import QtCore, QtGui, QtWidgets  # type: ignore

    try:
        from PySide.QtCore import Signal, Slot  # type: ignore
    except ImportError:  # pragma: no cover
        from PySide.QtCore import pyqtSignal as Signal, pyqtSlot as Slot  # type: ignore
except Exception as _exc1:  # noqa: BLE001
    try:
        from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
        from PySide6.QtCore import Signal, Slot  # type: ignore
    except Exception as _exc2:  # noqa: BLE001
        try:
            from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
            from PySide2.QtCore import Signal, Slot  # type: ignore
        except Exception as _exc3:  # noqa: BLE001
            _IMPORT_ERROR = _exc3 or _exc2 or _exc1
            QtCore = QtGui = QtWidgets = None  # type: ignore
            Signal = Slot = None  # type: ignore

# QAction moved between QtGui and QtWidgets across Qt versions.
if QtWidgets is not None and hasattr(QtWidgets, "QAction"):
    QAction = QtWidgets.QAction
elif QtGui is not None and hasattr(QtGui, "QAction"):
    QAction = QtGui.QAction
else:
    QAction = None


def require_qt() -> None:
    """Raise a clear error if Qt bindings are missing."""
    if QtWidgets is None:
        raise RuntimeError(
            "GrokCAD could not import PySide / PySide6 / PySide2. "
            "This workbench must run inside the FreeCAD GUI. "
            f"Last import error: {_IMPORT_ERROR!r}"
        )


def qt_version_tuple() -> tuple[int, int, int]:
    """Return the runtime Qt version as (major, minor, patch)."""
    require_qt()
    try:
        v = QtCore.qVersion()
        parts = [int(p) for p in str(v).split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return parts[0], parts[1], parts[2]
    except Exception:  # noqa: BLE001
        return (5, 15, 0)


def is_qt6() -> bool:
    return qt_version_tuple()[0] >= 6


def keep_aspect() -> Any:
    """Return the KeepAspectRatio enum value (Qt5/Qt6 compatible)."""
    require_qt()
    try:
        return QtCore.Qt.AspectRatioMode.KeepAspectRatio
    except AttributeError:
        return QtCore.Qt.KeepAspectRatio


def smooth_transform() -> Any:
    require_qt()
    try:
        return QtCore.Qt.TransformationMode.SmoothTransformation
    except AttributeError:
        return QtCore.Qt.SmoothTransformation


def align_right() -> Any:
    require_qt()
    try:
        return QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    except AttributeError:
        return QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter


def align_left() -> Any:
    require_qt()
    try:
        return QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
    except AttributeError:
        return QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter


def align_center() -> Any:
    require_qt()
    try:
        return QtCore.Qt.AlignmentFlag.AlignCenter
    except AttributeError:
        return QtCore.Qt.AlignCenter


def wa_delete_on_close() -> Any:
    require_qt()
    try:
        return QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
    except AttributeError:
        return QtCore.Qt.WA_DeleteOnClose


def dock_right() -> Any:
    require_qt()
    try:
        return QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    except AttributeError:
        return QtCore.Qt.RightDockWidgetArea


def dock_left() -> Any:
    require_qt()
    try:
        return QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
    except AttributeError:
        return QtCore.Qt.LeftDockWidgetArea


def key_return() -> Any:
    require_qt()
    try:
        return QtCore.Qt.Key.Key_Return
    except AttributeError:
        return QtCore.Qt.Key_Return


def key_enter() -> Any:
    require_qt()
    try:
        return QtCore.Qt.Key.Key_Enter
    except AttributeError:
        return QtCore.Qt.Key_Enter


def key_escape() -> Any:
    require_qt()
    try:
        return QtCore.Qt.Key.Key_Escape
    except AttributeError:
        return QtCore.Qt.Key_Escape


def modifier_ctrl() -> Any:
    require_qt()
    try:
        return QtCore.Qt.KeyboardModifier.ControlModifier
    except AttributeError:
        return QtCore.Qt.ControlModifier


def echo_password() -> Any:
    require_qt()
    try:
        return QtWidgets.QLineEdit.EchoMode.Password
    except AttributeError:
        return QtWidgets.QLineEdit.Password


def echo_normal() -> Any:
    require_qt()
    try:
        return QtWidgets.QLineEdit.EchoMode.Normal
    except AttributeError:
        return QtWidgets.QLineEdit.Normal


def text_selectable() -> Any:
    require_qt()
    try:
        return QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
    except AttributeError:
        return QtCore.Qt.TextSelectableByMouse
