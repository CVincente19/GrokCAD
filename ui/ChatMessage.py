# SPDX-License-Identifier: MIT
"""
Chat bubbles, tool-call cards, and screenshot thumbnails.

These widgets are dumb views: the panel pushes text / status into them.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional

from core.qtcompat import (
    QtCore,
    QtGui,
    QtWidgets,
    align_center,
    keep_aspect,
    require_qt,
    smooth_transform,
    text_selectable,
)
from ui.markdownutil import escape, markdown_to_html


def _elide(text: str, n: int = 140) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


class ScreenshotThumb(QtWidgets.QLabel):
    """Clickable PNG thumbnail. Click opens a full-size preview dialog."""

    def __init__(self, image: Dict[str, Any], parent=None) -> None:
        require_qt()
        super().__init__(parent)
        self._image = image
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor if hasattr(QtCore.Qt, "PointingHandCursor") else QtCore.Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"{image.get('view', 'view')} — click to enlarge")
        pix = _pixmap_from_image(image)
        if pix is not None and not pix.isNull():
            self._full = pix
            self.setPixmap(pix.scaled(160, 110, keep_aspect(), smooth_transform()))
        else:
            self._full = None
            self.setText(str(image.get("view") or "image"))
        self.setStyleSheet(
            "QLabel { background: #0b1220; border: 1px solid #2a3548; border-radius: 6px; padding: 2px; }"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._full is not None:
            _show_image_dialog(self._full, str(self._image.get("view") or "Screenshot"), self)
        super().mousePressEvent(event)


def _pixmap_from_image(image: Dict[str, Any]) -> Optional[QtGui.QPixmap]:
    require_qt()
    b64 = image.get("png_base64")
    path = image.get("path")
    pix = QtGui.QPixmap()
    if b64:
        try:
            raw = base64.b64decode(b64)
            pix.loadFromData(raw, "PNG")
            if not pix.isNull():
                return pix
        except Exception:  # noqa: BLE001
            pass
    if path:
        pix = QtGui.QPixmap(str(path))
        if not pix.isNull():
            return pix
    return None


def _show_image_dialog(pix: QtGui.QPixmap, title: str, parent) -> None:
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(min(1100, pix.width() + 40), min(800, pix.height() + 40))
    lay = QtWidgets.QVBoxLayout(dlg)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    lab = QtWidgets.QLabel()
    lab.setAlignment(align_center())
    lab.setPixmap(pix)
    scroll.setWidget(lab)
    lay.addWidget(scroll)
    btn = QtWidgets.QPushButton("Close")
    btn.clicked.connect(dlg.accept)
    lay.addWidget(btn, alignment=align_center())
    dlg.exec_()


class ToolCallCard(QtWidgets.QFrame):
    """Expandable card describing one tool invocation and its result."""

    approve_clicked = QtCore.Signal(str)  # tool_call id
    reject_clicked = QtCore.Signal(str)

    def __init__(self, tool_call: Dict[str, Any], parent=None) -> None:
        require_qt()
        super().__init__(parent)
        self.tool_call = tool_call
        self.call_id = str(tool_call.get("id") or "")
        fn = tool_call.get("function") or {}
        self.tool_name = str(fn.get("name") or "tool")
        raw_args = fn.get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                self.arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                self.arguments = {"_raw": raw_args}
        else:
            self.arguments = raw_args if isinstance(raw_args, dict) else {}

        self.setObjectName("ToolCallCard")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        self._status = QtWidgets.QLabel("queued")
        self._status.setObjectName("ToolStatus")
        title = QtWidgets.QLabel(f"<b>{escape(self.tool_name)}</b>")
        title.setTextFormat(QtCore.Qt.RichText if hasattr(QtCore.Qt, "RichText") else QtCore.Qt.TextFormat.RichText)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._status)
        root.addLayout(header)

        reason = self.arguments.get("reason")
        if reason:
            rlab = QtWidgets.QLabel(escape(str(reason)))
            rlab.setWordWrap(True)
            rlab.setObjectName("ToolReason")
            root.addWidget(rlab)

        self._body = QtWidgets.QPlainTextEdit()
        self._body.setReadOnly(True)
        self._body.setMaximumHeight(160)
        preview = self._preview_text()
        self._body.setPlainText(preview)
        self._body.setVisible(bool(preview.strip()))
        root.addWidget(self._body)

        self._result = QtWidgets.QPlainTextEdit()
        self._result.setReadOnly(True)
        self._result.setMaximumHeight(140)
        self._result.setVisible(False)
        root.addWidget(self._result)

        self._thumbs = QtWidgets.QHBoxLayout()
        root.addLayout(self._thumbs)

        btns = QtWidgets.QHBoxLayout()
        self._btn_approve = QtWidgets.QPushButton("Approve")
        self._btn_reject = QtWidgets.QPushButton("Reject")
        self._btn_copy = QtWidgets.QPushButton("Copy")
        self._btn_approve.clicked.connect(lambda: self.approve_clicked.emit(self.call_id))
        self._btn_reject.clicked.connect(lambda: self.reject_clicked.emit(self.call_id))
        self._btn_copy.clicked.connect(self._copy)
        btns.addWidget(self._btn_approve)
        btns.addWidget(self._btn_reject)
        btns.addStretch(1)
        btns.addWidget(self._btn_copy)
        root.addLayout(btns)
        self._btns = (self._btn_approve, self._btn_reject)

        self.set_status("pending")

    def _preview_text(self) -> str:
        if self.tool_name == "execute_python":
            return str(self.arguments.get("code") or "")
        hide = {"png_base64"}
        slim = {k: v for k, v in self.arguments.items() if k not in hide}
        try:
            return json.dumps(slim, indent=2, default=str)
        except Exception:  # noqa: BLE001
            return str(slim)

    def set_status(self, status: str) -> None:
        self._status.setText(status)
        self._status.setProperty("status", status)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        pending = status in {"pending", "queued", "awaiting approval"}
        for b in self._btns:
            b.setVisible(pending)
        colors = {
            "pending": "#c9a227",
            "queued": "#c9a227",
            "awaiting approval": "#c9a227",
            "running": "#3aa0ff",
            "ok": "#3dd68c",
            "error": "#ff6b6b",
            "rejected": "#ff6b6b",
            "skipped": "#8b97a8",
        }
        color = colors.get(status, "#8b97a8")
        self._status.setStyleSheet(f"color: {color}; font-weight: 600;")

    def set_result(self, result: Dict[str, Any]) -> None:
        ok = bool(result.get("ok", False)) if isinstance(result, dict) else False
        self.set_status("ok" if ok else "error")
        slim = dict(result) if isinstance(result, dict) else {"result": result}
        for key in ("png_base64",):
            slim.pop(key, None)
        if "images" in slim:
            slim["images"] = [
                {k: im.get(k) for k in ("view", "path", "width", "height", "bytes") if isinstance(im, dict)}
                for im in slim["images"]
            ]
        if "screenshots" in slim:
            slim["screenshots"] = [
                {k: im.get(k) for k in ("view", "path", "width", "height") if isinstance(im, dict)}
                for im in slim["screenshots"]
            ]
        try:
            text = json.dumps(slim, indent=2, default=str)
        except Exception:  # noqa: BLE001
            text = str(slim)
        if result.get("traceback"):
            text = (result.get("stderr") or "") + "\n\n" + result["traceback"]
        elif result.get("stdout"):
            text = (result.get("stdout") or "") + ("\n\n" + text if text else "")
        self._result.setPlainText(text[:8000])
        self._result.setVisible(True)

        images = []
        if isinstance(result, dict):
            images.extend(result.get("images") or [])
            images.extend(result.get("screenshots") or [])
        for im in images:
            if isinstance(im, dict):
                self._thumbs.addWidget(ScreenshotThumb(im, self))

    def set_awaiting(self) -> None:
        self.set_status("awaiting approval")

    def _copy(self) -> None:
        cb = QtWidgets.QApplication.clipboard()
        text = self._body.toPlainText() or json.dumps(self.arguments, indent=2, default=str)
        cb.setText(text)


class ChatMessageWidget(QtWidgets.QFrame):
    """One row in the transcript: user / assistant / system / tool-group."""

    approve_tool = QtCore.Signal(str)
    reject_tool = QtCore.Signal(str)

    def __init__(self, role: str, parent=None) -> None:
        require_qt()
        super().__init__(parent)
        self.role = role
        self.setObjectName(f"Msg_{role}")
        self._created = time.time()

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(3)

        meta = QtWidgets.QHBoxLayout()
        who = {"user": "You", "assistant": "Grok", "system": "System", "tool": "Tool"}.get(role, role)
        self._who = QtWidgets.QLabel(who)
        self._who.setObjectName("MsgWho")
        self._time = QtWidgets.QLabel(time.strftime("%H:%M:%S"))
        self._time.setObjectName("MsgTime")
        meta.addWidget(self._who)
        meta.addStretch(1)
        meta.addWidget(self._time)
        outer.addLayout(meta)

        self._browser = QtWidgets.QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._browser.setMinimumHeight(24)
        self._browser.setMaximumHeight(16777215)
        self._browser.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum
        )
        self._browser.document().setDocumentMargin(6)
        outer.addWidget(self._browser)

        self._image_row = QtWidgets.QHBoxLayout()
        outer.addLayout(self._image_row)

        self._tools_box = QtWidgets.QVBoxLayout()
        self._tools_box.setSpacing(6)
        outer.addLayout(self._tools_box)
        self._cards: Dict[str, ToolCallCard] = {}

        self._raw = ""
        self.set_text("")

    def set_text(self, text: str, *, markdown: bool = True) -> None:
        self._raw = text or ""
        if markdown and self.role in {"assistant", "system", "user"}:
            body = markdown_to_html(self._raw)
        else:
            body = f"<p>{escape(self._raw)}</p>"
        self._browser.setHtml(body)
        self._fit_browser()

    def append_text(self, piece: str) -> None:
        self.set_text(self._raw + (piece or ""))

    def add_images(self, images: List[Dict[str, Any]]) -> None:
        for im in images or []:
            if isinstance(im, dict):
                self._image_row.addWidget(ScreenshotThumb(im, self))

    def add_tool_card(self, tool_call: Dict[str, Any]) -> ToolCallCard:
        card = ToolCallCard(tool_call, self)
        card.approve_clicked.connect(self.approve_tool.emit)
        card.reject_clicked.connect(self.reject_tool.emit)
        self._tools_box.addWidget(card)
        cid = str(tool_call.get("id") or "")
        if cid:
            self._cards[cid] = card
        return card

    def card(self, call_id: str) -> Optional[ToolCallCard]:
        return self._cards.get(call_id)

    def _fit_browser(self) -> None:
        doc = self._browser.document()
        doc.setTextWidth(max(120, self._browser.viewport().width()))
        h = int(doc.size().height()) + 12
        self._browser.setFixedHeight(max(28, min(h, 900)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_browser()
