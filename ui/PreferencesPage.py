# SPDX-License-Identifier: MIT
"""
FreeCAD preference page: Edit → Preferences → Grok CAD Agent.

The page is registered from InitGui.py via
``Gui.addPreferencePage(GrokCADPreferencesPage, "Grok CAD Agent")``.
FreeCAD instantiates the class and calls ``saveSettings`` / ``loadSettings``.
"""

from __future__ import annotations

from core import prefs
from core.qtcompat import (
    QtWidgets,
    echo_normal,
    echo_password,
    require_qt,
)


class GrokCADPreferencesPage:
    """Python preference page (no .ui file)."""

    def __init__(self, parent=None) -> None:
        require_qt()
        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle("Grok CAD Agent")
        root = QtWidgets.QVBoxLayout(self.form)
        root.setContentsMargins(8, 8, 8, 8)

        root.addWidget(self._group_api())
        root.addWidget(self._group_model())
        root.addWidget(self._group_agent())
        root.addWidget(self._group_vision())
        root.addWidget(self._group_prompt())
        root.addStretch(1)

    # ------------------------------------------------------------------

    def _group_api(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("xAI API")
        form = QtWidgets.QFormLayout(g)
        self.edt_key = QtWidgets.QLineEdit()
        self.edt_key.setEchoMode(echo_password())
        self.edt_key.setPlaceholderText("xai-…  or leave empty to use $XAI_API_KEY")
        self.chk_show_key = QtWidgets.QCheckBox("Show key")
        self.chk_show_key.toggled.connect(self._toggle_key)
        key_row = QtWidgets.QHBoxLayout()
        key_row.addWidget(self.edt_key, 1)
        key_row.addWidget(self.chk_show_key)
        wrap = QtWidgets.QWidget()
        wrap.setLayout(key_row)
        form.addRow("API key", wrap)

        self.edt_base = QtWidgets.QLineEdit()
        self.edt_base.setPlaceholderText(prefs.DEFAULT_BASE_URL)
        form.addRow("Base URL", self.edt_base)

        hint = QtWidgets.QLabel(
            "Get a key at <a href='https://console.x.ai/'>console.x.ai</a>. "
            "Stored in FreeCAD parameters (not encrypted). "
            "Environment variables <code>XAI_API_KEY</code> / "
            "<code>GROK_API_KEY</code> override an empty field."
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        form.addRow(hint)
        return g

    def _group_model(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Model defaults")
        form = QtWidgets.QFormLayout(g)
        self.cmb_model = QtWidgets.QComboBox()
        self.cmb_model.setEditable(True)
        for m in prefs.KNOWN_MODELS:
            self.cmb_model.addItem(m)
        form.addRow("Preferred model", self.cmb_model)

        self.spin_temp = QtWidgets.QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setDecimals(2)
        form.addRow("Temperature", self.spin_temp)

        self.spin_tokens = QtWidgets.QSpinBox()
        self.spin_tokens.setRange(256, 128000)
        self.spin_tokens.setSingleStep(256)
        form.addRow("Max tokens", self.spin_tokens)

        self.spin_timeout = QtWidgets.QDoubleSpinBox()
        self.spin_timeout.setRange(15.0, 600.0)
        self.spin_timeout.setSuffix(" s")
        form.addRow("HTTP timeout", self.spin_timeout)

        self.chk_stream = QtWidgets.QCheckBox("Stream responses")
        form.addRow(self.chk_stream)
        return g

    def _group_agent(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Agent")
        form = QtWidgets.QFormLayout(g)
        self.cmb_mode = QtWidgets.QComboBox()
        for key, label in prefs.MODE_LABELS.items():
            self.cmb_mode.addItem(label, key)
        form.addRow("Default mode", self.cmb_mode)

        self.spin_iters = QtWidgets.QSpinBox()
        self.spin_iters.setRange(1, 40)
        form.addRow("Max tool iterations", self.spin_iters)

        self.cmb_dock = QtWidgets.QComboBox()
        self.cmb_dock.addItem("Right", "right")
        self.cmb_dock.addItem("Left", "left")
        form.addRow("Dock side (restart chat)", self.cmb_dock)

        self.chk_selection = QtWidgets.QCheckBox("Append current selection to each user message")
        form.addRow(self.chk_selection)
        return g

    def _group_vision(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Vision / screenshots")
        form = QtWidgets.QFormLayout(g)
        self.spin_w = QtWidgets.QSpinBox()
        self.spin_h = QtWidgets.QSpinBox()
        self.spin_w.setRange(320, 4096)
        self.spin_h.setRange(240, 4096)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.spin_w)
        row.addWidget(QtWidgets.QLabel("×"))
        row.addWidget(self.spin_h)
        row.addStretch(1)
        wrap = QtWidgets.QWidget()
        wrap.setLayout(row)
        form.addRow("Resolution", wrap)

        self.cmb_bg = QtWidgets.QComboBox()
        for bg in ("Current", "White", "Black", "Transparent"):
            self.cmb_bg.addItem(bg)
        form.addRow("Background", self.cmb_bg)

        self.chk_auto = QtWidgets.QCheckBox("Capture a screenshot after execute_python")
        form.addRow(self.chk_auto)

        self.edt_views = QtWidgets.QLineEdit()
        self.edt_views.setPlaceholderText("Isometric,Front,Top")
        form.addRow("Auto-screenshot views", self.edt_views)
        return g

    def _group_prompt(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Extra system instructions")
        lay = QtWidgets.QVBoxLayout(g)
        self.edt_extra = QtWidgets.QPlainTextEdit()
        self.edt_extra.setPlaceholderText(
            "Optional. Appended to the built-in mechanical-design system prompt.\n"
            "Example: Always use ISO metric threads. Company fillet default is 1.5 mm."
        )
        self.edt_extra.setFixedHeight(120)
        lay.addWidget(self.edt_extra)
        return g

    def _toggle_key(self, show: bool) -> None:
        self.edt_key.setEchoMode(echo_normal() if show else echo_password())

    # ------------------------------------------------------------------
    # FreeCAD hooks
    # ------------------------------------------------------------------

    def loadSettings(self) -> None:  # noqa: N802
        prefs.ensure_defaults()
        self.edt_key.setText(prefs.get_str("ApiKey", ""))
        self.edt_base.setText(prefs.get_base_url())
        model = prefs.get_model()
        if self.cmb_model.findText(model) < 0:
            self.cmb_model.addItem(model)
        self.cmb_model.setCurrentText(model)
        self.spin_temp.setValue(prefs.get_temperature())
        self.spin_tokens.setValue(prefs.get_max_tokens())
        self.spin_timeout.setValue(prefs.get_request_timeout())
        self.chk_stream.setChecked(prefs.get_bool("StreamResponses", True))
        mode = prefs.get_agent_mode()
        idx = self.cmb_mode.findData(mode)
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)
        self.spin_iters.setValue(prefs.get_max_tool_iterations())
        d = self.cmb_dock.findData(prefs.get_dock_side())
        if d >= 0:
            self.cmb_dock.setCurrentIndex(d)
        self.chk_selection.setChecked(prefs.get_bool("SendSelectionContext", True))
        w, h = prefs.get_screenshot_size()
        self.spin_w.setValue(w)
        self.spin_h.setValue(h)
        bg = prefs.get_screenshot_background()
        i = self.cmb_bg.findText(bg)
        if i >= 0:
            self.cmb_bg.setCurrentIndex(i)
        self.chk_auto.setChecked(prefs.get_auto_screenshot())
        self.edt_views.setText(",".join(prefs.get_auto_screenshot_views()))
        self.edt_extra.setPlainText(prefs.get_extra_instructions())

    def saveSettings(self) -> None:  # noqa: N802
        prefs.set_api_key(self.edt_key.text())
        prefs.set_str("BaseUrl", self.edt_base.text().strip() or prefs.DEFAULT_BASE_URL)
        prefs.set_str("Model", self.cmb_model.currentText().strip() or prefs.DEFAULT_MODEL)
        prefs.set_float("Temperature", float(self.spin_temp.value()))
        prefs.set_int("MaxTokens", int(self.spin_tokens.value()))
        prefs.set_float("RequestTimeout", float(self.spin_timeout.value()))
        prefs.set_bool("StreamResponses", self.chk_stream.isChecked())
        prefs.set_str("AgentMode", str(self.cmb_mode.currentData() or prefs.MODE_APPROVE_CODE))
        prefs.set_int("MaxToolIterations", int(self.spin_iters.value()))
        prefs.set_str("DockSide", str(self.cmb_dock.currentData() or "right"))
        prefs.set_bool("SendSelectionContext", self.chk_selection.isChecked())
        prefs.set_int("ScreenshotWidth", int(self.spin_w.value()))
        prefs.set_int("ScreenshotHeight", int(self.spin_h.value()))
        prefs.set_str("ScreenshotBackground", self.cmb_bg.currentText())
        prefs.set_bool("AutoScreenshotAfterCode", self.chk_auto.isChecked())
        prefs.set_str("AutoScreenshotViews", self.edt_views.text().strip() or "Isometric")
        prefs.set_str("ExtraInstructions", self.edt_extra.toPlainText())
