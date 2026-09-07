# SPDX-License-Identifier: MIT
"""
Dockable Grok CAD Agent chat panel.

This is the entire human-facing agent loop:

1. User types a design request (optionally attaching screenshots).
2. A :class:`GrokWorker` thread streams the model reply.
3. Tool calls are rendered as cards. Depending on the agent mode they
   either wait for approval or run immediately on the GUI thread.
4. Tool results (and viewport images) are fed back until Grok stops
   calling tools or the iteration cap is hit.

FreeCAD API usage stays on this thread. The worker never touches documents.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from core import attachments, log, prefs, screenshot, tools
from core.grok_client import (
    Conversation,
    GrokWorker,
    discover_models,
    load_system_prompt,
    parse_tool_arguments,
    pick_model,
)
from core.paths import CHAT_QSS_FILE
from core.qtcompat import (
    QtCore,
    QtGui,
    QtWidgets,
    dock_left,
    dock_right,
    key_enter,
    key_escape,
    key_return,
    modifier_ctrl,
    require_qt,
)
from core.safety import confirm_execution, scan_code
from ui.ChatMessage import ChatMessageWidget

# Module-level singleton so toolbar commands can find the live panel.
_PANEL_INSTANCE: Optional["GrokChatPanel"] = None


def get_panel() -> Optional["GrokChatPanel"]:
    return _PANEL_INSTANCE


def ensure_panel(mw=None) -> "GrokChatPanel":
    """Create or re-show the dock widget on the FreeCAD main window."""
    global _PANEL_INSTANCE
    require_qt()
    if _PANEL_INSTANCE is not None:
        try:
            _PANEL_INSTANCE.show()
            _PANEL_INSTANCE.raise_()
            return _PANEL_INSTANCE
        except RuntimeError:
            _PANEL_INSTANCE = None
    if mw is None:
        try:
            import FreeCADGui as Gui  # type: ignore

            mw = Gui.getMainWindow()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"FreeCAD main window not available: {exc}") from exc
    panel = GrokChatPanel(mw)
    side = dock_left() if prefs.get_dock_side() == "left" else dock_right()
    mw.addDockWidget(side, panel)
    try:
        # Default to a narrow strip; user can drag the splitter wider.
        orient = getattr(QtCore.Qt, "Horizontal", None) or QtCore.Qt.Orientation.Horizontal
        mw.resizeDocks([panel], [300], orient)
    except Exception:  # noqa: BLE001
        pass
    panel.show()
    _PANEL_INSTANCE = panel
    return panel


class _InputEdit(QtWidgets.QPlainTextEdit):
    """Composer: Ctrl+Enter sends, Enter inserts a newline, Esc stops."""

    send_requested = QtCore.Signal()
    stop_requested = QtCore.Signal()
    files_dropped = QtCore.Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if key in (key_return(), key_enter()) and mods & modifier_ctrl():
            self.send_requested.emit()
            event.accept()
            return
        if key == key_escape():
            self.stop_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class GrokChatPanel(QtWidgets.QDockWidget):
    """Right-hand (or left-hand) agent dock."""

    def __init__(self, parent=None) -> None:
        require_qt()
        super().__init__("Grok CAD Agent", parent)
        self.setObjectName("GrokCADChatPanel")
        self.setAllowedAreas(dock_left() | dock_right())
        prefs.ensure_defaults()

        self.conversation = Conversation()
        self._worker: Optional[GrokWorker] = None
        self._busy = False
        self._tool_iter = 0
        self._pending: List[Dict[str, Any]] = []
        self._pending_cards_host: Optional[ChatMessageWidget] = None
        self._stream_widget: Optional[ChatMessageWidget] = None
        self._attach_shots: List[Dict[str, Any]] = []
        self._user_files: List[Dict[str, Any]] = []
        self._file_text: str = ""
        self._pending_vision: List[Dict[str, Any]] = []
        self._last_usage = {}
        self._wait_started = 0.0
        self._wait_timer = QtCore.QTimer(self)
        self._wait_timer.setInterval(1000)
        self._wait_timer.timeout.connect(self._tick_wait)

        container = QtWidgets.QWidget()
        container.setObjectName("GrokCADRoot")
        container.setMinimumWidth(240)
        self.setMinimumWidth(260)
        self.setWidget(container)
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_transcript(), 1)
        root.addWidget(self._build_composer())
        root.addWidget(self._build_status())

        self._apply_qss()
        self._refresh_models(initial=True)
        self._sync_controls_from_prefs()
        self._hello()

        log.add_listener(self._on_log)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        self.cmb_mode = QtWidgets.QComboBox()
        for key, label in prefs.MODE_LABELS.items():
            self.cmb_mode.addItem(label, key)
        self.cmb_mode.setToolTip(
            "Plan only: no tools.\n"
            "Approve every tool: you confirm each call.\n"
            "Auto-run safe + approve code: reads/screenshots run alone; Python needs Yes.\n"
            "Full auto: only flagged-dangerous Python is confirmed."
        )
        self.cmb_mode.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self.btn_settings = QtWidgets.QToolButton()
        self.btn_settings.setText("⚙")
        self.btn_settings.setCheckable(True)
        self.btn_settings.setToolTip("Show model / temperature / session controls")
        self.btn_settings.toggled.connect(self._toggle_settings)
        self.btn_attach = QtWidgets.QToolButton()
        self.btn_attach.setText("📎")
        self.btn_attach.setToolTip("Attach images or PDFs (drawings with dimensions)")
        self.btn_attach.clicked.connect(self.attach_files)
        self.btn_shot = QtWidgets.QToolButton()
        self.btn_shot.setText("📷")
        self.btn_shot.setToolTip("Capture the 3D view for Grok")
        self.btn_shot.clicked.connect(self.take_screenshot_for_grok)
        top.addWidget(self.cmb_mode, 1)
        top.addWidget(self.btn_settings)
        top.addWidget(self.btn_attach)
        top.addWidget(self.btn_shot)
        col.addLayout(top)

        self.settings = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(self.settings)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)

        self.cmb_model = QtWidgets.QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.setMinimumWidth(80)
        self.cmb_model.setToolTip("Grok model. grok-4.6 is preferred when available.")

        self.spin_temp = QtWidgets.QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setDecimals(2)
        self.spin_temp.setToolTip("Sampling temperature. 0.1–0.3 is best for CAD.")

        self.spin_tokens = QtWidgets.QSpinBox()
        self.spin_tokens.setRange(256, 128000)
        self.spin_tokens.setSingleStep(256)
        self.spin_tokens.setToolTip("Maximum completion tokens per API call.")

        self.chk_vision = QtWidgets.QCheckBox("View")
        self.chk_vision.setToolTip("Send an isometric screenshot with the next user message.")
        self.chk_vision.setChecked(False)

        grid.addWidget(QtWidgets.QLabel("Model"), 0, 0)
        grid.addWidget(self.cmb_model, 0, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Temp"), 1, 0)
        grid.addWidget(self.spin_temp, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Tok"), 1, 2)
        grid.addWidget(self.spin_tokens, 1, 3)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("New")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_export = QtWidgets.QPushButton("Export")
        self.btn_refresh = QtWidgets.QPushButton("Models")
        self.btn_new.setToolTip("New design session")
        self.btn_clear.setToolTip("Clear chat")
        self.btn_export.setToolTip("Export conversation")
        self.btn_refresh.setToolTip("Refresh model list from xAI")
        self.btn_new.clicked.connect(self.new_session)
        self.btn_clear.clicked.connect(self.clear_chat)
        self.btn_export.clicked.connect(self.export_conversation)
        self.btn_refresh.clicked.connect(lambda: self._refresh_models(initial=False))
        for b in (self.btn_new, self.btn_clear, self.btn_export, self.btn_refresh):
            b.setMaximumHeight(24)
            btn_row.addWidget(b)
        btn_row.addWidget(self.chk_vision)
        grid.addLayout(btn_row, 2, 0, 1, 4)

        self.settings.setVisible(False)
        col.addWidget(self.settings)

        self.cmb_mode.currentIndexChanged.connect(self._persist_controls)
        self.cmb_model.currentTextChanged.connect(self._persist_controls)
        self.spin_temp.valueChanged.connect(self._persist_controls)
        self.spin_tokens.valueChanged.connect(self._persist_controls)
        return wrap

    def _toggle_settings(self, on: bool) -> None:
        self.settings.setVisible(bool(on))

    def _build_transcript(self) -> QtWidgets.QWidget:
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff if hasattr(QtCore.Qt, "ScrollBarAlwaysOff") else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._transcript_host = QtWidgets.QWidget()
        self._transcript_host.setObjectName("Transcript")
        self._tlay = QtWidgets.QVBoxLayout(self._transcript_host)
        self._tlay.setContentsMargins(2, 2, 2, 2)
        self._tlay.setSpacing(8)
        self._tlay.addStretch(1)
        self.scroll.setWidget(self._transcript_host)
        return self.scroll

    def _build_composer(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self.attach_host = QtWidgets.QWidget()
        self.attach_row = QtWidgets.QHBoxLayout(self.attach_host)
        self.attach_row.setContentsMargins(0, 0, 0, 0)
        self.attach_row.setSpacing(4)
        self.attach_host.setVisible(False)
        lay.addWidget(self.attach_host)

        self.input = _InputEdit()
        self.input.setPlaceholderText("Describe the part…  Ctrl+Enter send")
        self.input.setFixedHeight(56)
        self.input.setMaximumHeight(80)
        self.input.send_requested.connect(self.send_user_message)
        self.input.stop_requested.connect(self.stop_generation)
        self.input.files_dropped.connect(self.add_file_paths)
        lay.addWidget(self.input)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(3)
        self.btn_send = QtWidgets.QPushButton("Send")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_approve = QtWidgets.QPushButton("Approve")
        self.btn_reject = QtWidgets.QPushButton("Reject")
        for b in (self.btn_send, self.btn_stop, self.btn_approve, self.btn_reject):
            b.setMaximumHeight(24)
        self.btn_send.clicked.connect(self.send_user_message)
        self.btn_stop.clicked.connect(self.stop_generation)
        self.btn_approve.clicked.connect(self.approve_pending)
        self.btn_reject.clicked.connect(self.reject_pending)
        self.btn_stop.setEnabled(False)
        self.btn_approve.setEnabled(False)
        self.btn_reject.setEnabled(False)
        row.addWidget(self.btn_send)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_approve)
        row.addWidget(self.btn_reject)
        lay.addLayout(row)
        return box

    def _build_status(self) -> QtWidgets.QLabel:
        self.lbl_status = QtWidgets.QLabel("Ready.")
        self.lbl_status.setObjectName("StatusStrip")
        self.lbl_status.setWordWrap(True)
        return self.lbl_status

    def _apply_qss(self) -> None:
        try:
            qss = CHAT_QSS_FILE.read_text(encoding="utf-8")
            self.setStyleSheet(qss)
        except Exception as exc:  # noqa: BLE001
            log.warn(f"Could not load chat.qss: {exc}")
            self.setStyleSheet(
                "#GrokCADRoot { background: #10161f; color: #e7eef7; }"
            )

    # ------------------------------------------------------------------
    # Prefs / models
    # ------------------------------------------------------------------

    def _sync_controls_from_prefs(self) -> None:
        self.spin_temp.blockSignals(True)
        self.spin_tokens.blockSignals(True)
        self.cmb_mode.blockSignals(True)
        self.spin_temp.setValue(prefs.get_temperature())
        self.spin_tokens.setValue(prefs.get_max_tokens())
        mode = prefs.get_agent_mode()
        idx = self.cmb_mode.findData(mode)
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)
        self.spin_temp.blockSignals(False)
        self.spin_tokens.blockSignals(False)
        self.cmb_mode.blockSignals(False)
        model = prefs.get_model()
        if self.cmb_model.findText(model) < 0:
            self.cmb_model.addItem(model)
        self.cmb_model.setCurrentText(model)

    def _persist_controls(self, *_args) -> None:
        prefs.set_str("Model", self.cmb_model.currentText().strip())
        prefs.set_float("Temperature", float(self.spin_temp.value()))
        prefs.set_int("MaxTokens", int(self.spin_tokens.value()))
        prefs.set_str("AgentMode", str(self.cmb_mode.currentData() or prefs.MODE_APPROVE_CODE))
        self.conversation.model = self.cmb_model.currentText().strip()

    def _refresh_models(self, *, initial: bool = False) -> None:
        current = self.cmb_model.currentText().strip() or prefs.get_model()
        models = list(prefs.KNOWN_MODELS)
        if not initial and prefs.get_api_key():
            try:
                models = discover_models()
            except Exception as exc:  # noqa: BLE001
                log.warn(f"Model list failed: {exc}")
        self.cmb_model.blockSignals(True)
        self.cmb_model.clear()
        for m in models:
            self.cmb_model.addItem(m)
        chosen = pick_model(current, models)
        if self.cmb_model.findText(chosen) < 0:
            self.cmb_model.addItem(chosen)
        self.cmb_model.setCurrentText(chosen)
        self.cmb_model.blockSignals(False)
        if not initial:
            self._set_status(f"Models: {', '.join(models[:8])}{'…' if len(models) > 8 else ''}")

    # ------------------------------------------------------------------
    # Transcript helpers
    # ------------------------------------------------------------------

    def _hello(self) -> None:
        w = self._add_message("system")
        key = prefs.get_api_key()
        key_state = "API key is set." if key else "No API key yet — open Edit → Preferences → Grok CAD Agent."
        w.set_text(
            "Ready. Describe a part, or **📎 attach** a drawing (PNG/JPG/PDF).\n\n"
            f"- {key_state}\n"
            "- Ctrl+Enter sends. 📎 images/PDFs. 📷 viewport. ⚙ settings."
        )

    def _add_message(self, role: str) -> ChatMessageWidget:
        w = ChatMessageWidget(role, self._transcript_host)
        w.approve_tool.connect(self._approve_one)
        w.reject_tool.connect(self._reject_one)
        # Insert above the trailing stretch.
        self._tlay.insertWidget(self._tlay.count() - 1, w)
        QtCore.QTimer.singleShot(30, self._scroll_to_bottom)
        return w

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _token_suffix(self) -> str:
        """Human-readable token line. 8192 is the *output* cap, not input."""
        cap = int(self.spin_tokens.value()) if hasattr(self, "spin_tokens") else prefs.get_max_tokens()
        inn = int(self._last_usage.get("prompt_tokens") or 0)
        out = int(self._last_usage.get("completion_tokens") or 0)
        if not inn and not out:
            return f"  ·  output cap {cap:,}"
        return f"  ·  in {inn:,} + out {out:,}  (output cap {cap:,})"

    def _set_status(self, text: str) -> None:
        self.lbl_status.setText(text + self._token_suffix())
        self.lbl_status.setToolTip(
            "“in” is the prompt (system + tools + images + chat). "
            "“out” is Grok’s reply. Max tokens only limits “out”, not “in”."
        )
        log.info(text)

    def _tick_wait(self) -> None:
        if not self._busy:
            self._wait_timer.stop()
            return
        elapsed = int(time.time() - self._wait_started) if self._wait_started else 0
        self.lbl_status.setText(
            f"Waiting for Grok… {elapsed}s  (first token can take 15–60s on grok-4.6)"
            + self._token_suffix()
        )

    def _on_log(self, level: str, message: str) -> None:
        if level == "error":
            # Don't fight the more specific status updates during a run.
            if not self._busy:
                self.lbl_status.setText(message)

    # ------------------------------------------------------------------
    # Public actions (toolbar + commands)
    # ------------------------------------------------------------------

    def new_session(self) -> None:
        if self._busy:
            self.stop_generation()
        self.conversation.reset()
        self._clear_widgets()
        self._pending.clear()
        self._attach_shots.clear()
        self._user_files.clear()
        self._file_text = ""
        self._pending_vision.clear()
        self._refresh_attach_strip()
        self._hello()
        self._set_status("New design session.")

    def clear_chat(self) -> None:
        if self._busy:
            self.stop_generation()
        self.conversation.messages.clear()
        self._clear_widgets()
        self._pending.clear()
        self._pending_vision.clear()
        self._user_files.clear()
        self._file_text = ""
        self._refresh_attach_strip()
        self._hello()
        self._set_status("Chat cleared (system prompt kept).")

    def _clear_widgets(self) -> None:
        while self._tlay.count() > 1:
            item = self._tlay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stream_widget = None
        self._pending_cards_host = None

    def export_conversation(self) -> None:
        path, selected = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export conversation",
            str(_default_export_name()),
            "Markdown (*.md);;JSON (*.json)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".json") or "JSON" in (selected or ""):
                data = self.conversation.to_json()
                # Strip API-adjacent secrets just in case.
                if path.lower().endswith(".json") is False:
                    path = path + ".json"
            else:
                data = self.conversation.to_markdown()
                if not path.lower().endswith(".md"):
                    path = path + ".md"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data)
            self._set_status(f"Exported to {path}")
        except Exception as exc:  # noqa: BLE001
            self._error_banner(f"Export failed: {exc}")

    def take_screenshot_for_grok(self, views: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        cap = screenshot.capture_views(views or ["Isometric", "Front", "Top", "Right"])
        images = cap.get("images") or []
        if not images:
            self._error_banner(cap.get("error") or "Screenshot failed (is a document open?)")
            return []
        host = self._add_message("system")
        host.set_text(f"Captured {len(images)} viewport image(s) for Grok.")
        host.add_images(images)
        self._attach_shots.extend(images)
        self.chk_vision.setChecked(True)
        self._set_status(f"Queued {len(images)} screenshot(s) for the next message.")
        return images

    def attach_files(self) -> None:
        paths, _flt = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Attach drawings for Grok",
            os.path.expanduser("~"),
            "Drawings (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.pdf);;"
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;"
            "PDF (*.pdf);;All files (*)",
        )
        if paths:
            self.add_file_paths(paths)

    def add_file_paths(self, paths: List[str]) -> None:
        rec = attachments.load_paths(paths)
        for err in rec.get("errors") or []:
            log.warn(err)
        added = rec.get("images") or []
        if added:
            self._user_files.extend(added)
        if rec.get("text"):
            extra = rec["text"]
            self._file_text = (self._file_text + "\n\n" + extra).strip() if self._file_text else extra
        self._refresh_attach_strip()
        if rec.get("errors") and not added and not rec.get("text"):
            self._error_banner("; ".join(rec["errors"]))
            return
        msg = "Queued %d image(s)" % len(added)
        if rec.get("text"):
            msg += " + extracted PDF text"
        if rec.get("errors"):
            msg += " (%d warning(s))" % len(rec["errors"])
        self._set_status(msg + ".")

    def _refresh_attach_strip(self) -> None:
        while self.attach_row.count():
            item = self.attach_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        n = len(self._user_files)
        has_text = bool(self._file_text)
        self.attach_host.setVisible(n > 0 or has_text)
        if n == 0 and not has_text:
            return
        lab = QtWidgets.QLabel("%d file(s)%s" % (n, " + PDF text" if has_text else ""))
        lab.setWordWrap(True)
        self.attach_row.addWidget(lab, 1)
        clear = QtWidgets.QPushButton("×")
        clear.setFixedWidth(22)
        clear.setMaximumHeight(22)
        clear.setToolTip("Remove attachments")
        clear.clicked.connect(self._clear_user_files)
        self.attach_row.addWidget(clear)

    def _clear_user_files(self) -> None:
        self._user_files.clear()
        self._file_text = ""
        self._refresh_attach_strip()

    def approve_pending(self) -> None:
        if not self._pending:
            self._set_status("Nothing waiting for approval.")
            return
        batch = list(self._pending)
        self._pending.clear()
        self._set_approve_enabled(False)
        self._run_tool_batch(batch, approved=True)

    def reject_pending(self) -> None:
        if not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        self._set_approve_enabled(False)
        self._run_tool_batch(batch, approved=False)

    def _approve_one(self, call_id: str) -> None:
        item = self._pop_pending(call_id)
        if item:
            self._run_tool_batch([item], approved=True)

    def _reject_one(self, call_id: str) -> None:
        item = self._pop_pending(call_id)
        if item:
            self._run_tool_batch([item], approved=False)

    def _pop_pending(self, call_id: str) -> Optional[Dict[str, Any]]:
        for i, tc in enumerate(self._pending):
            if tc.get("id") == call_id:
                return self._pending.pop(i)
        return None

    def _set_approve_enabled(self, on: bool) -> None:
        self.btn_approve.setEnabled(on)
        self.btn_reject.setEnabled(on)

    # ------------------------------------------------------------------
    # Send / stream
    # ------------------------------------------------------------------

    def send_user_message(self) -> None:
        if self._busy:
            self._set_status("Already running — Stop first, or wait.")
            return
        text = self.input.toPlainText().strip()
        has_files = bool(self._user_files or self._file_text)
        if not text and not has_files:
            return
        if not text:
            text = "Please read the attached drawing(s) and recreate the part parametrically in FreeCAD. Use the printed dimensions."
        if not prefs.get_api_key():
            self._error_banner(
                "No xAI API key. Open Edit → Preferences → Grok CAD Agent "
                "and paste a key from https://console.x.ai/"
            )
            return

        images: List[Dict[str, Any]] = []
        if self._user_files:
            images.extend(self._user_files)
            self._user_files = []
        if self._file_text:
            text = text + "\n\n[Extracted text from attached PDF]\n" + self._file_text
            self._file_text = ""
        self._refresh_attach_strip()
        if self.chk_vision.isChecked() or self._attach_shots:
            if self._attach_shots:
                images.extend(self._attach_shots)
                self._attach_shots = []
            else:
                cap = screenshot.capture_views(["Isometric"])
                images.extend(cap.get("images") or [])
            self.chk_vision.setChecked(False)

        # Optional selection context — cheap and hugely useful.
        if prefs.get_bool("SendSelectionContext", True):
            try:
                from core.document_state import get_selection, summarize_document

                sel = get_selection()
                if sel:
                    text = text + "\n\n[Current selection]\n" + json.dumps(sel, default=str)
                # Tiny state blurb so Grok knows which document it is in.
                state = summarize_document()
                if state.get("ok"):
                    names = [o.get("name") for o in state.get("objects", [])][:40]
                    text += (
                        f"\n\n[Active document: {state.get('name')} — "
                        f"{state.get('object_count')} objects: {', '.join(n for n in names if n)}]"
                    )
            except Exception:  # noqa: BLE001
                pass

        user_w = self._add_message("user")
        user_w.set_text(self.input.toPlainText().strip() or text)
        if images:
            user_w.add_images(images)
        self.input.clear()
        self.conversation.add_user(text, images=images or None)
        self._tool_iter = 0
        self._start_turn()

    def _start_turn(self) -> None:
        if self._tool_iter >= prefs.get_max_tool_iterations():
            self._error_banner(
                f"Stopped after {self._tool_iter} tool iterations "
                "(Preferences → Max tool iterations)."
            )
            self._set_busy(False)
            return
        mode = str(self.cmb_mode.currentData() or prefs.get_agent_mode())
        plan_only = mode == prefs.MODE_PLAN_ONLY
        self._set_busy(True)
        self._stream_widget = self._add_message("assistant")
        self._stream_widget.set_text("_Connecting to xAI…_")
        self._wait_started = time.time()
        self._wait_timer.start()
        self._set_status("Contacting Grok…")

        worker = GrokWorker(
            self.conversation.api_messages(),
            model=self.cmb_model.currentText().strip(),
            temperature=float(self.spin_temp.value()),
            max_tokens=int(self.spin_tokens.value()),
            plan_only=plan_only,
            stream=prefs.get_bool("StreamResponses", True),
            parent=self,
        )
        worker.started.connect(self._on_worker_started)
        worker.chunk.connect(self._on_chunk)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_worker_started(self, message: str) -> None:
        if self._stream_widget is not None and (self._stream_widget._raw or "").startswith("_Connecting"):
            self._stream_widget.set_text(f"_{message}_")
        self._set_status(message)

    def _on_chunk(self, piece: str) -> None:
        self._wait_timer.stop()
        if self._stream_widget is not None:
            if (self._stream_widget._raw or "").startswith("_"):
                self._stream_widget.set_text("")
            self._stream_widget.append_text(piece)
            self._scroll_to_bottom()
        self._set_status("Grok is writing…")

    def _on_failed(self, message: str) -> None:
        self._wait_timer.stop()
        self._set_busy(False)
        if self._stream_widget is not None and not (self._stream_widget._raw or "").strip():
            self._stream_widget.set_text(f"**Error.** {message}")
        self._error_banner(message)

    def _on_completed(self, result: object) -> None:
        self._wait_timer.stop()
        data = result if isinstance(result, dict) else {}
        content = data.get("content") or ""
        tool_calls = data.get("tool_calls") or []
        usage = data.get("usage") or {}
        if usage:
            self._last_usage = usage
            self.conversation.usage_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.conversation.usage_completion_tokens += int(usage.get("completion_tokens") or 0)
        if data.get("model"):
            self.conversation.model = data["model"]
        if self._stream_widget is not None:
            # Stream already filled the widget; make sure final text matches.
            if content and content != self._stream_widget._raw:
                self._stream_widget.set_text(content)

        mode = str(self.cmb_mode.currentData() or prefs.get_agent_mode())
        # Plan-only: never persist tool_calls into the transcript. An
        # assistant message with tool_calls and no matching tool results
        # makes the next API request invalid.
        store_calls = tool_calls if (tool_calls and mode != prefs.MODE_PLAN_ONLY) else None
        self.conversation.add_assistant(content, tool_calls=store_calls)

        if not tool_calls:
            self._set_busy(False)
            self._set_status("Grok finished this turn.")
            return

        if mode == prefs.MODE_PLAN_ONLY:
            host = self._stream_widget or self._add_message("assistant")
            for tc in tool_calls:
                card = host.add_tool_card(tc)
                card.set_status("skipped")
            host.append_text(
                "\n\n_(Plan-only mode: tool calls were not executed. "
                "Switch mode to run them.)_"
            )
            self._set_busy(False)
            self._set_status("Plan only — tools not executed.")
            return

        host = self._stream_widget or self._add_message("assistant")
        auto: List[Dict[str, Any]] = []
        wait: List[Dict[str, Any]] = []
        for tc in tool_calls:
            card = host.add_tool_card(tc)
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            args = parse_tool_arguments(fn.get("arguments"))
            if tools.needs_confirmation(name, args, mode):
                card.set_awaiting()
                wait.append(tc)
            else:
                card.set_status("queued")
                auto.append(tc)
        self._pending_cards_host = host
        self._pending = wait
        self._set_approve_enabled(bool(wait))
        if auto:
            # Run safe tools now. If anything is still waiting for approval,
            # _run_tool_batch will NOT start the next API turn — the OpenAI
            # protocol requires a tool result for every call_id first.
            self._run_tool_batch(auto, approved=True)
        elif wait:
            self._set_busy(False)
            self._set_status(f"{len(wait)} tool call(s) waiting for approval.")
        else:
            self._set_busy(False)

    def _run_tool_batch(self, batch: List[Dict[str, Any]], *, approved: bool) -> None:
        """Execute (or reject) tools on the GUI thread, then continue the loop."""
        images_for_vision: List[Dict[str, Any]] = []
        host = self._pending_cards_host
        ctx = tools.ToolContext()
        ctx.parent_widget = self
        ctx.agent_mode = str(self.cmb_mode.currentData() or prefs.get_agent_mode())
        ctx.on_images = lambda imgs: images_for_vision.extend(imgs or [])

        for tc in batch:
            fn = tc.get("function") or {}
            name = fn.get("name") or "unknown"
            args = parse_tool_arguments(fn.get("arguments"))
            call_id = tc.get("id") or ""
            card = host.card(call_id) if host is not None else None
            if not approved:
                result = {"ok": False, "error": "Rejected by user.", "rejected": True}
                if card:
                    card.set_result(result)
                    card.set_status("rejected")
                self.conversation.add_tool_result(call_id, name, result)
                continue

            # Extra modal only when the static scan flags danger. Ordinary
            # Python is already gated by agent mode + the tool card.
            if name == "execute_python":
                report = scan_code(str(args.get("code") or ""))
                if report.dangerous:
                    body = args.get("code") or ""
                    if report.issues:
                        body = "Safety notes:\n- " + "\n- ".join(report.issues) + "\n\n" + body
                    ok = confirm_execution(
                        "Approve Grok Python",
                        body,
                        dangerous=report.dangerous,
                        parent=self,
                    )
                    if not ok:
                        result = {"ok": False, "error": "Rejected by user.", "rejected": True}
                        if card:
                            card.set_result(result)
                            card.set_status("rejected")
                        self.conversation.add_tool_result(call_id, name, result)
                        continue

            if card:
                card.set_status("running")
            QtWidgets.QApplication.processEvents()
            t0 = time.time()
            result = tools.execute_tool(name, args, ctx)
            result["elapsed_ms"] = int((time.time() - t0) * 1000)
            if card:
                card.set_result(result)
            self.conversation.add_tool_result(call_id, name, result)
            # Harvest screenshots from the result itself if the callback missed them.
            for key in ("images", "screenshots"):
                for im in result.get(key) or []:
                    if isinstance(im, dict) and im.get("png_base64"):
                        images_for_vision.append(im)

        if images_for_vision:
            self._pending_vision.extend(images_for_vision)

        # Do not start the next Grok turn until every tool_call from the
        # last assistant message has a corresponding tool result.
        if self._pending:
            self._set_busy(False)
            self._set_approve_enabled(True)
            self._set_status(f"{len(self._pending)} tool call(s) waiting for approval.")
            return

        # Vision attachments must come *after* every tool result, never
        # interleaved, or the xAI/OpenAI tool protocol rejects the request.
        self._flush_pending_vision()
        self._tool_iter += 1
        self._start_turn()

    def _flush_pending_vision(self) -> None:
        if not self._pending_vision:
            return
        seen = set()
        unique: List[Dict[str, Any]] = []
        for im in self._pending_vision:
            p = im.get("path") or id(im)
            if p in seen:
                continue
            seen.add(p)
            unique.append(im)
        self._pending_vision = []
        if unique:
            self.conversation.add_tool_images_as_user(unique)

    def stop_generation(self) -> None:
        self._wait_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(800)
        self._set_busy(False)
        self._set_status("Stopped.")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_send.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.input.setEnabled(True)

    def _error_banner(self, message: str) -> None:
        w = self._add_message("system")
        w.set_text(f"**Error.** {message}")
        self._set_status(message)
        log.error(message)

    # ------------------------------------------------------------------
    # Qt
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        global _PANEL_INSTANCE
        try:
            log.remove_listener(self._on_log)
        except Exception:  # noqa: BLE001
            pass
        self.stop_generation()
        if _PANEL_INSTANCE is self:
            _PANEL_INSTANCE = None
        super().closeEvent(event)


def _default_export_name() -> str:
    from core.paths import session_dir

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return str(session_dir() / f"grokcad_{stamp}.md")
