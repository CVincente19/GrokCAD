# SPDX-License-Identifier: MIT
"""
High-quality multi-view viewport capture for Grok vision.

Captures the active 3D view from named standard cameras, encodes PNG as
base64, and optionally keeps a copy under the user data directory.

All FreeCADGui calls happen on the calling thread — invoke this from the
GUI thread only.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import log, prefs
from .paths import screenshot_dir

# Camera helpers on FreeCADGui.ActiveDocument.ActiveView
VIEW_METHODS = {
    "Isometric": "viewIsometric",
    "Iso": "viewIsometric",
    "Dimetric": "viewDimetric",
    "Trimetric": "viewTrimetric",
    "Front": "viewFront",
    "Back": "viewRear",
    "Rear": "viewRear",
    "Top": "viewTop",
    "Bottom": "viewBottom",
    "Left": "viewLeft",
    "Right": "viewRight",
    "Axonometric": "viewAxonometric",
}

STANDARD_VIEWS = (
    "Isometric",
    "Front",
    "Top",
    "Right",
    "Left",
    "Bottom",
    "Back",
    "Dimetric",
    "Trimetric",
)


def _gui():
    import FreeCADGui  # type: ignore

    return FreeCADGui


def active_view():
    Gui = _gui()
    if not Gui.ActiveDocument:
        return None
    return Gui.ActiveDocument.ActiveView


def set_camera_view(view_name: str, *, fit: bool = True) -> Dict[str, Any]:
    """Move the active camera to a named standard view."""
    view = active_view()
    if view is None:
        return {"ok": False, "error": "No active 3D view."}
    method = VIEW_METHODS.get(_norm(view_name))
    if method is None or not hasattr(view, method):
        return {
            "ok": False,
            "error": f"Unknown view {view_name!r}.",
            "available": list(STANDARD_VIEWS),
        }
    try:
        getattr(view, method)()
        if fit and hasattr(view, "fitAll"):
            view.fitAll()
        _process_events()
        return {"ok": True, "view": _norm(view_name)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def capture_views(
    views: Optional[Sequence[str]] = None,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    background: Optional[str] = None,
    restore_camera: bool = True,
    fit: bool = True,
    max_bytes: int = 3_500_000,
) -> Dict[str, Any]:
    """
    Capture one or more named views.

    Returns::

        {
          "ok": True,
          "images": [
             {
               "view": "Isometric",
               "mime": "image/png",
               "width": 1600,
               "height": 1200,
               "png_base64": "...",
               "path": "/home/.../GrokCAD/screenshots/....png",
               "bytes": 12345,
             },
             ...
          ],
          "errors": []
        }
    """
    view = active_view()
    if view is None:
        return {"ok": False, "error": "No active 3D view. Open a document first.", "images": []}

    if width is None or height is None:
        w, h = prefs.get_screenshot_size()
        width = width or w
        height = height or h
    background = background or prefs.get_screenshot_background()
    wanted = list(views) if views else ["Isometric"]
    if not wanted:
        wanted = ["Isometric"]

    saved_cam = None
    if restore_camera:
        try:
            saved_cam = view.getCamera()
        except Exception:  # noqa: BLE001
            saved_cam = None

    images: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        for name in wanted:
            rec = _capture_one(
                view,
                name,
                width=int(width),
                height=int(height),
                background=background,
                fit=fit,
                max_bytes=max_bytes,
            )
            if rec.get("ok"):
                images.append(rec)
            else:
                errors.append(f"{name}: {rec.get('error', 'unknown error')}")
    finally:
        if saved_cam is not None:
            try:
                view.setCamera(saved_cam)
                _process_events()
            except Exception as exc:  # noqa: BLE001
                log.warn(f"Could not restore camera: {exc}")

    return {
        "ok": bool(images) and not errors,
        "images": images,
        "errors": errors,
        "count": len(images),
    }


def capture_current(
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    background: Optional[str] = None,
    max_bytes: int = 3_500_000,
) -> Dict[str, Any]:
    """Capture the current camera without changing it."""
    return capture_views(
        ["Current"],
        width=width,
        height=height,
        background=background,
        restore_camera=False,
        fit=False,
        max_bytes=max_bytes,
    )


def _capture_one(
    view,
    name: str,
    *,
    width: int,
    height: int,
    background: str,
    fit: bool,
    max_bytes: int,
) -> Dict[str, Any]:
    key = _norm(name)
    if key not in {"Current", "current"}:
        method = VIEW_METHODS.get(key)
        if method is None or not hasattr(view, method):
            return {"ok": False, "error": f"Unknown view {name!r}"}
        try:
            getattr(view, method)()
            if fit and hasattr(view, "fitAll"):
                view.fitAll()
            _process_events()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Failed to set view {name}: {exc}"}

    out_dir = screenshot_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}_{key}_{width}x{height}.png"

    # Preferred: Coin/Quarter saveImage — highest quality, offscreen capable.
    saved = False
    last_err = ""
    if hasattr(view, "saveImage"):
        for bg in _background_candidates(background):
            try:
                view.saveImage(str(path), int(width), int(height), bg)
                if path.is_file() and path.stat().st_size > 0:
                    saved = True
                    background = bg
                    break
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
    if not saved:
        # Fallback: grab the on-screen framebuffer via Qt.
        try:
            img = _grab_framebuffer(view, width, height)
            if img is None:
                return {
                    "ok": False,
                    "error": f"saveImage failed ({last_err}) and framebuffer grab returned nothing.",
                }
            img.save(str(path), "PNG")
            saved = True
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Capture failed: {last_err or exc}"}

    encoded, w, h, nbytes = _encode_png(path, max_bytes=max_bytes)
    return {
        "ok": True,
        "view": key,
        "mime": "image/png",
        "width": w,
        "height": h,
        "png_base64": encoded,
        "path": str(path),
        "bytes": nbytes,
        "background": background,
    }


def _background_candidates(preferred: str) -> List[str]:
    pref = (preferred or "Current").strip()
    # FreeCAD accepts Current / White / Black / Transparent (build-dependent).
    ordered = [pref]
    for alt in ("Current", "White", "Black", "Transparent"):
        if alt not in ordered:
            ordered.append(alt)
    return ordered


def _grab_framebuffer(view, width: int, height: int):
    """Grab the 3D view widget and scale to the requested size."""
    from .qtcompat import QtGui, keep_aspect, require_qt, smooth_transform

    require_qt()
    widget = None
    for attr in ("getViewer",):
        if hasattr(view, attr):
            try:
                viewer = view.getViewer()
                if hasattr(viewer, "getWidget"):
                    widget = viewer.getWidget()
                elif hasattr(viewer, "widget"):
                    widget = viewer.widget()
            except Exception:  # noqa: BLE001
                widget = None
    if widget is None:
        # Active view itself is often a QWidget subclass.
        widget = getattr(view, "graphicsView", None) or view
    if widget is None or not hasattr(widget, "grab"):
        return None
    try:
        pix = widget.grab()
    except Exception:  # noqa: BLE001
        return None
    img = pix.toImage()
    if img.isNull():
        return None
    if img.width() != width or img.height() != height:
        img = img.scaled(int(width), int(height), keep_aspect(), smooth_transform())
    return img


def _encode_png(path: Path, *, max_bytes: int) -> Tuple[str, int, int, int]:
    """
    Read *path*, optionally downscale so the base64 payload stays under
    *max_bytes*, return (b64, width, height, raw_bytes).
    """
    data = path.read_bytes()
    w, h = _png_size(data)

    if len(data) <= max_bytes:
        return base64.b64encode(data).decode("ascii"), w, h, len(data)

    # Downscale with Pillow if present, else Qt, else just send what we have.
    try:
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(data))
        img = img.convert("RGBA")
        scale = 0.85
        while True:
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            raw = buf.getvalue()
            if len(raw) <= max_bytes or img.width < 400:
                path.write_bytes(raw)
                return (
                    base64.b64encode(raw).decode("ascii"),
                    img.width,
                    img.height,
                    len(raw),
                )
            nw = max(320, int(img.width * scale))
            nh = max(240, int(img.height * scale))
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"Pillow downscale skipped: {exc}")

    try:
        from .qtcompat import QtGui, keep_aspect, require_qt, smooth_transform

        require_qt()
        qimg = QtGui.QImage(str(path))
        scale = 0.85
        while qimg.width() > 400:
            qimg = qimg.scaled(
                int(qimg.width() * scale),
                int(qimg.height() * scale),
                keep_aspect(),
                smooth_transform(),
            )
            buf = QtGui.QByteArray()
            buff = QtCore_Buffer(buf)
            qimg.save(buff, "PNG")  # type: ignore
            raw = bytes(buf)
            if len(raw) <= max_bytes:
                path.write_bytes(raw)
                return (
                    base64.b64encode(raw).decode("ascii"),
                    qimg.width(),
                    qimg.height(),
                    len(raw),
                )
    except Exception as exc:  # noqa: BLE001
        log.warn(f"Qt downscale skipped: {exc}")

    # Last resort: send the original even if large.
    return base64.b64encode(data).decode("ascii"), w, h, len(data)


def QtCore_Buffer(qba):
    from .qtcompat import QtCore

    buff = QtCore.QBuffer(qba)
    mode = None
    for candidate in (
        lambda: QtCore.QIODevice.OpenModeFlag.WriteOnly,
        lambda: QtCore.QIODeviceBase.OpenModeFlag.WriteOnly,
        lambda: QtCore.QIODevice.WriteOnly,
    ):
        try:
            mode = candidate()
            break
        except Exception:  # noqa: BLE001
            continue
    buff.open(mode)
    return buff


def _png_size(data: bytes) -> Tuple[int, int]:
    # PNG IHDR: 8 byte sig + 4 len + 4 'IHDR' + 4 width + 4 height
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return w, h
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def _process_events() -> None:
    """Let Coin / Qt paint the new camera before grabbing."""
    try:
        from .qtcompat import QtWidgets, require_qt

        require_qt()
        QtWidgets.QApplication.processEvents()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.03)


def _norm(name: str) -> str:
    if not name:
        return "Isometric"
    n = name.strip()
    # Title-case common aliases
    mapping = {k.lower(): k for k in VIEW_METHODS}
    mapping["current"] = "Current"
    mapping["iso"] = "Isometric"
    mapping["isometric"] = "Isometric"
    mapping["rear"] = "Back"
    mapping["back"] = "Back"
    return mapping.get(n.lower(), n[:1].upper() + n[1:])


def images_to_openai_content(images: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert capture_views() images into OpenAI vision content parts."""
    parts: List[Dict[str, Any]] = []
    for img in images:
        b64 = img.get("png_base64")
        if not b64:
            continue
        label = img.get("view", "view")
        parts.append({"type": "text", "text": f"[Screenshot: {label} {img.get('width')}x{img.get('height')}]"})
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            }
        )
    return parts
