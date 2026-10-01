"""Main window. Holds widgets only; all media logic lives in ``shot2exr.converter``."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from shot2exr import TOOL_NAME, __version__, config, naming
from shot2exr.cli import format_inspection, format_plan
from shot2exr.color_manager import ColorConfig, load_config
from shot2exr.converter import ConversionPlan, ConversionResult, Inspection, inspect_source, plan_conversion
from shot2exr.errors import Shot2EXRError, ValidationError
from shot2exr.gui.styles import ERROR, MUTED, OK, STATE_COLORS, WARN
from shot2exr.gui.workers import ConversionTask, EngineTask
from shot2exr.models import ConversionRequest, DetectionState, Resolution, ResizeMode
from shot2exr.output_paths import OutputLocation, resolve_output_location
from shot2exr.settings import Settings, current_platform, load_settings, save_settings

AUTO = config.DEFAULT_INPUT_COLORSPACE


def _value_label() -> QLabel:
    label = QLabel("-")
    label.setObjectName("value")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{TOOL_NAME} {__version__}")
        self.setAcceptDrops(True)
        self.resize(1280, 860)
        self._pool = QThreadPool.globalInstance()
        self._tasks: set[EngineTask] = set()  # keep signal owners alive until finished
        self._cfg: ColorConfig | None = None
        self._inspection: Inspection | None = None
        self._inspected_path: str | None = None
        self._qsettings = QSettings()
        self._settings = Settings()
        self._settings_error: str | None = None
        self._location: OutputLocation | None = None
        self._conversion: ConversionTask | None = None
        self._report_path: Path | None = None

        left = QWidget()
        left_layout = QVBoxLayout(left)
        for group in (self._build_source(), self._build_shot(), self._build_color(), self._build_output()):
            left_layout.addWidget(group)
        left_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(scroll)
        splitter.addWidget(self._build_conversion())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Drop a MOV/MP4 file or an EXR folder to begin.")
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Check environment", self._check_environment)

        self._load_studio_settings()
        self._load_ocio_config()
        self._update_preview()

    # ------------------------------------------------------------------ layout

    def _build_source(self) -> QGroupBox:
        box = QGroupBox("SOURCE")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Video file (.mov/.mp4) or EXR sequence folder - drag and drop supported")
        self.input_edit.textChanged.connect(self._on_input_changed)
        self.input_edit.returnPressed.connect(self.inspect_source)
        browse_file = QPushButton("Browse File...")
        browse_file.clicked.connect(self._browse_file)
        browse_dir = QPushButton("Browse Directory...")
        browse_dir.clicked.connect(self._browse_dir)
        self.inspect_btn = QPushButton("Inspect Source")
        self.inspect_btn.clicked.connect(self.inspect_source)
        row.addWidget(self.input_edit, 1)
        for w in (browse_file, browse_dir, self.inspect_btn):
            row.addWidget(w)
        lay.addLayout(row)

        grid = QGridLayout()
        self.src_labels: dict[str, QLabel] = {}
        fields = ["Type", "Resolution", "Frames", "Frame range", "FPS", "Missing frames", "Colour space", "Detection"]
        for i, name in enumerate(fields):
            caption = QLabel(name)
            caption.setObjectName("muted")
            self.src_labels[name] = _value_label()
            grid.addWidget(caption, i // 2, (i % 2) * 2)
            grid.addWidget(self.src_labels[name], i // 2, (i % 2) * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        self.src_message = QLabel("")
        self.src_message.setWordWrap(True)
        lay.addWidget(self.src_message)
        return box

    def _build_shot(self) -> QGroupBox:
        box = QGroupBox("SHOT INFORMATION")
        form = QFormLayout(box)
        self.project_edit = QLineEdit()
        self.shot_edit = QLineEdit()
        self.task_edit = QLineEdit()
        self.element_edit = QLineEdit(str(self._qsettings.value("element", "") or ""))
        self.element_edit.setPlaceholderText("e.g. water, fire, smoke, environment")
        self.element_edit.setToolTip("Folder just above the version directory. Not the same as Task.")
        self.version_edit = QLineEdit("001")
        self.project_edit.setPlaceholderText("e.g. PROJ")
        self.shot_edit.setPlaceholderText("e.g. 0010_020")
        self.task_edit.setPlaceholderText("e.g. comp")
        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, config.MAX_FRAME_NUMBER)
        self.start_spin.setValue(config.DEFAULT_START_FRAME)
        for label, w in (("Project", self.project_edit), ("Shot", self.shot_edit), ("Task", self.task_edit),
                         ("Element / Subtask", self.element_edit), ("Version", self.version_edit), ("Start Frame", self.start_spin)):
            form.addRow(label, w)
        for edit in (self.project_edit, self.shot_edit, self.task_edit, self.element_edit, self.version_edit):
            edit.textChanged.connect(self._update_preview)
        self.start_spin.valueChanged.connect(self._update_preview)
        return box

    def _build_color(self) -> QGroupBox:
        box = QGroupBox("COLOR MANAGEMENT")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.ocio_edit = QLineEdit()
        self.ocio_edit.setPlaceholderText("Empty = $OCIO, else built-in ACES studio config")
        self.ocio_edit.editingFinished.connect(self._load_ocio_config)
        ocio_browse = QPushButton("Browse...")
        ocio_browse.clicked.connect(self._browse_ocio)
        row.addWidget(self.ocio_edit, 1)
        row.addWidget(ocio_browse)
        form.addRow("OCIO Config", row)
        self.ocio_label = QLabel("-")
        self.ocio_label.setObjectName("muted")
        self.ocio_label.setWordWrap(True)
        form.addRow("", self.ocio_label)
        self.input_cs = QComboBox()
        self.output_cs = QComboBox()
        for combo in (self.input_cs, self.output_cs):
            combo.setMaxVisibleItems(25)
        self.input_cs.currentTextChanged.connect(self._update_detection_display)
        form.addRow("Input Color Space", self.input_cs)
        form.addRow("Output Color Space", self.output_cs)
        self.detect_label = QLabel("Not inspected")
        self.detect_label.setWordWrap(True)
        form.addRow("Detection", self.detect_label)
        self.accept_inferred = QCheckBox("Confirm INFERRED input colour space")
        self.accept_inferred.setEnabled(False)
        form.addRow("", self.accept_inferred)
        self.metadata_btn = QPushButton("Show Source Colour Metadata...")
        self.metadata_btn.setEnabled(False)
        self.metadata_btn.clicked.connect(self._show_metadata)
        form.addRow("", self.metadata_btn)
        return box

    def _build_output(self) -> QGroupBox:
        box = QGroupBox("OUTPUT SETTINGS")
        form = QFormLayout(box)
        root_row = QHBoxLayout()
        self.root_label = QLabel("-")
        self.root_label.setWordWrap(True)
        settings_btn = QPushButton("Settings...")
        settings_btn.clicked.connect(self._edit_settings)
        root_row.addWidget(self.root_label, 1)
        root_row.addWidget(settings_btn)
        form.addRow("Projects Root", root_row)
        self.resolved_edit = QLineEdit()
        self.resolved_edit.setReadOnly(True)
        self.resolved_edit.setPlaceholderText("Generated from Project, Shot, Task, Element and Version")
        form.addRow("Output Directory", self.resolved_edit)
        self.manual_check = QCheckBox("Manual output directory override (advanced)")
        self.manual_check.toggled.connect(self._on_manual_toggled)
        form.addRow("", self.manual_check)
        row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Exact destination directory (used only when the override is enabled)")
        self.output_edit.textChanged.connect(self._update_preview)
        self.out_browse = QPushButton("Browse...")
        self.out_browse.clicked.connect(self._browse_output)
        row.addWidget(self.output_edit, 1)
        row.addWidget(self.out_browse)
        form.addRow("", row)
        self._on_manual_toggled(False)

        res_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        for spin, val in ((self.width_spin, config.DEFAULT_RESOLUTION[0]), (self.height_spin, config.DEFAULT_RESOLUTION[1])):
            spin.setRange(1, 32768)
            spin.setValue(val)
            spin.valueChanged.connect(self._update_preview)
        self.use_source_res = QPushButton("Use Source")
        self.use_source_res.setEnabled(False)
        self.use_source_res.clicked.connect(self._use_source_resolution)
        res_row.addWidget(QLabel("W"))
        res_row.addWidget(self.width_spin)
        res_row.addWidget(QLabel("H"))
        res_row.addWidget(self.height_spin)
        res_row.addWidget(self.use_source_res)
        res_row.addStretch(1)
        form.addRow("Resolution", res_row)
        self.resize_combo = QComboBox()
        for mode in ResizeMode:
            self.resize_combo.addItem(mode.value.capitalize(), mode)
        form.addRow("Resize Mode", self.resize_combo)
        self.overwrite_check = QCheckBox("Overwrite existing output files")
        self.overwrite_check.setToolTip("Off by default. Existing frames of this version are only replaced when enabled.")
        form.addRow("", self.overwrite_check)
        self.preview_label = QLabel("-")
        self.preview_label.setObjectName("preview")
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Filename Preview", self.preview_label)
        self.range_label = _value_label()
        form.addRow("Output Frame Range", self.range_label)
        return box

    def _build_conversion(self) -> QGroupBox:
        box = QGroupBox("CONVERSION")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.dry_run_btn = QPushButton("Inspect / Dry Run")
        self.dry_run_btn.clicked.connect(self.dry_run)
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start_conversion)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        for w in (self.dry_run_btn, self.start_btn, self.cancel_btn):
            row.addWidget(w)
        lay.addLayout(row)
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.frame_label = QLabel("0 / 0")
        self.frame_label.setObjectName("muted")
        prog.addWidget(self.progress, 1)
        prog.addWidget(self.frame_label)
        lay.addLayout(prog)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace", 9))
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.log, 1)
        self.result_label = QLabel("Inspect the source, run a dry run, then start the conversion.")
        self.result_label.setObjectName("muted")
        self.result_label.setWordWrap(True)
        lay.addWidget(self.result_label)
        row2 = QHBoxLayout()
        self.open_folder_btn = QPushButton("Open Output Folder")
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        self.open_report_btn = QPushButton("Open Conversion Report")
        self.open_report_btn.setEnabled(False)
        self.open_report_btn.clicked.connect(self._open_report)
        row2.addWidget(self.open_folder_btn)
        row2.addWidget(self.open_report_btn)
        lay.addLayout(row2)
        self._update_open_buttons()
        return box

    # ------------------------------------------------------------------ helpers

    def _check_environment(self) -> None:
        from shot2exr import diagnostics

        self._log("Checking dependencies (OpenImageIO, OpenColorIO, FFmpeg, Qt, settings)...")
        self._run(diagnostics.run_checks, self._on_environment_checked,
                  ocio_config=self.ocio_edit.text().strip() or None, include_gui=False)

    def _on_environment_checked(self, checks) -> None:
        from shot2exr import diagnostics

        self._log(diagnostics.format_checks(checks))

    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _run(self, fn, on_success, *args, **kwargs) -> None:
        task = EngineTask(fn, *args, **kwargs)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(self._on_task_failed)
        task.signals.finished.connect(lambda t=task: self._on_task_finished(t))
        self._tasks.add(task)
        self._set_busy(True)
        self._pool.start(task)

    def _set_busy(self, busy: bool) -> None:
        self.inspect_btn.setEnabled(not busy)
        self.dry_run_btn.setEnabled(not busy)
        self.start_btn.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)  # indeterminate until the frame count is known
        elif self.progress.maximum() == 0:
            self.progress.setRange(0, 100)

    def _on_task_finished(self, task: EngineTask) -> None:
        self._tasks.discard(task)
        if not self._tasks:
            self._set_busy(False)

    def _on_task_failed(self, message: str) -> None:
        self._log(f"ERROR: {message}\n")
        self._set_result(message.splitlines()[0], ERROR)
        self.statusBar().showMessage("Failed - see log.")

    def _set_result(self, text: str, color: str) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------ OCIO

    def _load_ocio_config(self) -> None:
        explicit = self.ocio_edit.text().strip() or None
        try:
            cfg = load_config(explicit)
        except Shot2EXRError as exc:
            self._cfg = None
            self.ocio_label.setText(str(exc))
            self.ocio_label.setStyleSheet(f"color: {ERROR};")
            self.input_cs.clear()
            self.output_cs.clear()
            self._log(f"ERROR: {exc}")
            return
        self._cfg = cfg
        self.ocio_label.setStyleSheet("")
        self.ocio_label.setText(f"{cfg.name or 'unnamed'}  ({cfg.origin}: {cfg.source})")
        prev_in, prev_out = self.input_cs.currentText(), self.output_cs.currentText()
        names = cfg.colorspace_names()
        self.input_cs.blockSignals(True)
        self.input_cs.clear()
        self.input_cs.addItem(AUTO)
        self.input_cs.addItems(names)
        self.input_cs.setCurrentText(prev_in if prev_in in names else AUTO)
        self.input_cs.blockSignals(False)
        self.output_cs.clear()
        self.output_cs.addItems(names)
        default_out = cfg.find(prev_out) or cfg.find(config.DEFAULT_OUTPUT_COLORSPACE)
        if default_out:
            self.output_cs.setCurrentText(default_out)
        # A different config can change the detection mapping: re-inspect if we had a source.
        if self._inspection is not None:
            self.inspect_source()
        else:
            self._update_detection_display()

    # ------------------------------------------------------------------ source

    def _on_input_changed(self, text: str) -> None:
        if self._inspected_path is not None and text.strip() != self._inspected_path:
            self._inspection = None
            self._inspected_path = None
            self._show_inspection(None)

    def set_input_path(self, path: str, inspect: bool = True) -> None:
        self.input_edit.setText(path)
        if inspect:
            self.inspect_source()

    def inspect_source(self) -> None:
        path = self.input_edit.text().strip()
        if not path:
            self._set_result("Select an input file or directory first.", WARN)
            return
        if self._cfg is None:
            self._set_result("Fix the OCIO configuration first.", ERROR)
            return
        self._log(f"Inspecting {path} ...")
        self.statusBar().showMessage("Inspecting source...")
        self._run(inspect_source, lambda r, p=path: self._on_inspected(p, r), path, self._cfg)

    def _on_inspected(self, path: str, insp: Inspection) -> None:
        if path != self.input_edit.text().strip():
            return  # input changed while the worker ran
        self._inspection, self._inspected_path = insp, path
        self._show_inspection(insp)
        self._log(format_inspection(insp) + "\n")
        self.statusBar().showMessage("Source inspected.")
        self._set_result("Source inspected." if not insp.source.errors else "Source has problems - see log.",
                         OK if not insp.source.errors else ERROR)

    def _show_inspection(self, insp: Inspection | None) -> None:
        L = self.src_labels
        for label in L.values():
            label.setText("-")
            label.setStyleSheet("")
        self.src_message.setText("")
        self.metadata_btn.setEnabled(insp is not None)
        self.use_source_res.setEnabled(bool(insp and insp.source.resolution))
        if insp is not None:
            s, d = insp.source, insp.detection
            L["Type"].setText("EXR sequence" if s.sequence else "Video")
            L["Resolution"].setText(str(s.resolution) if s.resolution else "-")
            count = "-" if s.frame_count is None else (str(s.frame_count) if s.frame_count_exact else f"~{s.frame_count} (estimated)")
            L["Frames"].setText(count)
            if s.frame_range:
                L["Frame range"].setText(f"{s.frame_range[0]}-{s.frame_range[1]}")
            L["FPS"].setText(s.fps or "-")
            if s.missing_frames:
                L["Missing frames"].setText(naming.format_frame_ranges(s.missing_frames))
                L["Missing frames"].setStyleSheet(f"color: {ERROR};")
            elif s.sequence:
                L["Missing frames"].setText("none")
            L["Colour space"].setText(d.colorspace or "-")
            L["Detection"].setText(d.state.value)
            L["Detection"].setStyleSheet(f"color: {STATE_COLORS[d.state.value]}; font-weight: bold;")
            msgs = [f"<span style='color:{ERROR}'>ERROR: {e}</span>" for e in s.errors]
            msgs += [f"<span style='color:{WARN}'>WARNING: {w}</span>" for w in s.warnings]
            self.src_message.setText("<br>".join(msgs))
        self._update_detection_display()
        self._update_preview()

    def _update_detection_display(self) -> None:
        insp = self._inspection
        manual = self.input_cs.currentText() not in ("", AUTO)
        if insp is None:
            self.detect_label.setText("Not inspected")
            self.detect_label.setStyleSheet(f"color: {MUTED};")
            self.accept_inferred.setEnabled(False)
            return
        d = insp.detection
        color = STATE_COLORS[d.state.value]
        text = f"<b>{d.state.value}</b>: {d.colorspace or 'no proposal'}<br>{d.explanation}"
        if manual:
            text = f"<b>Manual override:</b> {self.input_cs.currentText()}<br>Detected: {d.state.value} {d.colorspace or ''}"
            color = OK
        elif d.state is DetectionState.UNKNOWN:
            text += "<br><b>Select the input colour space manually.</b>"
        elif d.state is DetectionState.INFERRED:
            text += "<br><b>Confirm below or choose the input colour space manually.</b>"
        self.detect_label.setText(text)
        self.detect_label.setStyleSheet(f"color: {color};")
        self.accept_inferred.setEnabled(not manual and d.state is DetectionState.INFERRED)
        if not self.accept_inferred.isEnabled():
            self.accept_inferred.setChecked(False)

    def _use_source_resolution(self) -> None:
        if self._inspection and self._inspection.source.resolution:
            res = self._inspection.source.resolution
            self.width_spin.setValue(res.width)
            self.height_spin.setValue(res.height)

    def _show_metadata(self) -> None:
        if not self._inspection:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Source Colour Metadata")
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("monospace", 9))
        payload = {
            "color_metadata": self._inspection.source.color_metadata,
            "detection": self._inspection.detection.to_dict(),
            "ocio": self._inspection.ocio,
        }
        text.setPlainText(json.dumps(payload, indent=2, default=str))
        lay.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.exec()

    # ------------------------------------------------------------------ naming preview

    def _update_preview(self) -> None:
        self._update_location()
        start = self.start_spin.value()
        try:
            base = naming.output_basename(self.project_edit.text(), self.shot_edit.text(), self.task_edit.text(),
                                          self.version_edit.text())
        except ValidationError as exc:
            self.preview_label.setText(exc.message)
            self.preview_label.setStyleSheet(f"color: {ERROR}; font-family: sans-serif; font-size: 10pt;")
            self.range_label.setText("-")
            return
        self.preview_label.setStyleSheet("")
        count = self._inspection.source.frame_count if self._inspection else None
        first = naming.output_filename(base, start)
        if count:
            last_frame = naming.output_frame_range(start, count)[1]
            self.preview_label.setText(f"{first}\n...\n{naming.output_filename(base, last_frame)}")
            self.range_label.setText(f"{start}-{last_frame}  ({count} frames)")
        else:
            self.preview_label.setText(first)
            self.range_label.setText(f"{start}-?  (inspect the source to get the frame count)")

    # ------------------------------------------------------------------ dry run

    def build_request(self) -> ConversionRequest:
        return ConversionRequest(
            input_path=Path(self.input_edit.text().strip()) if self.input_edit.text().strip() else "",
            project=self.project_edit.text(),
            shot=self.shot_edit.text(),
            task=self.task_edit.text(),
            version=self.version_edit.text(),
            start_frame=self.start_spin.value(),
            output_resolution=Resolution(self.width_spin.value(), self.height_spin.value()),
            element=self.element_edit.text(),
            output_directory=self._manual_directory(),
            input_colorspace=self.input_cs.currentText() or AUTO,
            output_colorspace=self.output_cs.currentText(),
            ocio_config=self.ocio_edit.text().strip() or None,
            resize_mode=self.resize_combo.currentData(),
            accept_inferred_colorspace=self.accept_inferred.isChecked(),
            overwrite=self.overwrite_check.isChecked(),
            dry_run=True,
        )

    def dry_run(self) -> None:
        if self._cfg is None:
            self._set_result("Fix the OCIO configuration first.", ERROR)
            return
        request = self.build_request()
        reuse = self._inspection if self._inspected_path == self.input_edit.text().strip() else None
        self._log("Dry run ...")
        self.statusBar().showMessage("Running dry run...")
        if self.element_edit.text().strip():
            self._qsettings.setValue("element", self.element_edit.text().strip())
        self._run(plan_conversion, self._on_plan, request, self._cfg, reuse, self._settings)

    def _on_plan(self, plan: ConversionPlan) -> None:
        if plan.inspection is not None and self._inspection is None:
            self._inspection, self._inspected_path = plan.inspection, str(plan.request.input_path)
            self._show_inspection(plan.inspection)
        self._log(format_plan(plan) + "\n")
        n = plan.frame_count or 0
        self.frame_label.setText(f"0 / {n}")
        if plan.ok:
            self._set_result(f"Dry run OK: {n} frames would be written to {plan.location.directory}. "
                             "Ready to convert.", OK)
        else:
            self._set_result(f"Dry run found {len(plan.errors)} problem(s): {plan.errors[0].message}", ERROR)
        self.statusBar().showMessage("Dry run finished.")

    # ------------------------------------------------------------------ conversion

    def start_conversion(self) -> None:
        if self._cfg is None:
            self._set_result("Fix the OCIO configuration first.", ERROR)
            return
        request = self.build_request()
        request.dry_run = False
        reuse = self._inspection if self._inspected_path == self.input_edit.text().strip() else None
        if self.element_edit.text().strip():
            self._qsettings.setValue("element", self.element_edit.text().strip())
        task = ConversionTask(request, self._cfg, reuse, self._settings)
        task.signals.planned.connect(self._on_conversion_planned)
        task.signals.progress.connect(self._on_progress)
        task.signals.succeeded.connect(self._on_conversion_done)
        task.signals.failed.connect(self._on_task_failed)
        task.signals.finished.connect(lambda t=task: self._on_conversion_finished(t))
        self._conversion = task
        self._tasks.add(task)
        self._set_busy(True)
        self.cancel_btn.setEnabled(True)
        self.open_report_btn.setEnabled(False)
        self._report_path = None
        self.progress.setRange(0, 0)
        self._log("Starting conversion ...")
        self.statusBar().showMessage("Converting...")
        self._pool.start(task)

    def cancel_conversion(self) -> None:
        if getattr(self, "_conversion", None) is not None:
            self._conversion.cancel()
            self.cancel_btn.setEnabled(False)
            self._log("Cancelling after the current frame ...")

    def _on_conversion_planned(self, plan: ConversionPlan) -> None:
        if not plan.ok:
            self._on_plan(plan)
            return
        self._log(format_plan(plan).replace("PLAN (dry run, nothing written)", "PLAN") + "\n")
        self.progress.setRange(0, plan.frame_count or 0)
        self.progress.setValue(0)
        self.frame_label.setText(f"0 / {plan.frame_count}")

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(done)
        self.frame_label.setText(f"{done} / {total}")
        self.statusBar().showMessage(f"Wrote {name}")

    def _on_conversion_done(self, result: ConversionResult) -> None:
        self._report_path = result.report_path
        self.open_report_btn.setEnabled(bool(result.report_path and Path(result.report_path).is_file()))
        if result.ok:
            self._set_result(f"Conversion complete: {result.frames_written} frames in {result.output_directory}", OK)
            self._log(f"SUCCESS: {result.frames_written} frames written to {result.output_directory}")
        else:
            self._set_result(f"Conversion {result.status}: {'; '.join(result.errors)}. No partial frames were kept.",
                             WARN if result.status == "cancelled" else ERROR)
            self._log(f"{result.status.upper()}: {'; '.join(result.errors)}")
        self._log(f"Report: {result.report_path}\n")
        self._update_open_buttons()

    def _on_conversion_finished(self, task: ConversionTask) -> None:
        self._conversion = None
        self.cancel_btn.setEnabled(False)
        self._on_task_finished(task)

    def _open_report(self) -> None:
        if getattr(self, "_report_path", None):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_path)))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if getattr(self, "_conversion", None) is not None:
            self._conversion.cancel()  # partial output is cleaned up by the engine
            self._pool.waitForDone(60000)
        super().closeEvent(event)

    # ------------------------------------------------------------------ browse / open

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select video or EXR frame", self._start_dir(),
                                              "Media (*.mov *.mp4 *.MOV *.MP4 *.exr *.EXR);;All files (*)")
        if path:
            self.set_input_path(path)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select EXR sequence directory", self._start_dir())
        if path:
            self.set_input_path(path)

    def _browse_ocio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select OCIO config", "", "OCIO config (*.ocio *.ocioz);;All files (*)")
        if path:
            self.ocio_edit.setText(path)
            self._load_ocio_config()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _start_dir(self) -> str:
        current = Path(self.input_edit.text().strip())
        try:
            return str(current.parent if current.is_file() else current) if self.input_edit.text().strip() else ""
        except OSError:
            return ""

    def _update_open_buttons(self) -> None:
        loc = self._location
        try:
            folder_exists = bool(loc) and Path(loc.directory).is_dir()
        except OSError:  # unreadable mount: the dry run reports it; the preview must not crash
            folder_exists = False
        self.open_folder_btn.setEnabled(folder_exists)

    def _open_output_folder(self) -> None:
        if self._location:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._location.directory)))

    # ------------------------------------------------------------------ output location

    def _manual_directory(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text) if self.manual_check.isChecked() and text else None

    def _on_manual_toggled(self, checked: bool) -> None:
        self.output_edit.setEnabled(checked)
        self.out_browse.setEnabled(checked)
        if hasattr(self, "preview_label"):
            self._update_preview()

    def _load_studio_settings(self) -> None:
        try:
            self._settings, self._settings_error = load_settings(), None
        except Shot2EXRError as exc:
            self._settings, self._settings_error = Settings(), str(exc)
            self._log(f"ERROR: {exc}")
        root = self._settings.projects_root()
        self.root_label.setText(root or f"not configured for {current_platform()} (click Settings...)")
        self.root_label.setStyleSheet("" if root else f"color: {WARN};")

    def _update_location(self) -> None:
        """Resolve the version directory with the shared builder and show it (read-only)."""
        self._location = None
        if self._settings_error and not self.manual_check.isChecked():
            message = self._settings_error
        else:
            try:
                self._location = resolve_output_location(
                    self.project_edit.text(), self.shot_edit.text(), self.task_edit.text(), self.element_edit.text(),
                    self.version_edit.text(), settings=self._settings, manual_directory=self._manual_directory(),
                )
                message = None
            except Shot2EXRError as exc:
                message = exc.message
        if self._location:
            self.resolved_edit.setText(str(self._location.directory))
            self.resolved_edit.setStyleSheet("")
            self.resolved_edit.setToolTip(f"{self._location.origin} directory")
        else:
            self.resolved_edit.setText(message or "")
            self.resolved_edit.setStyleSheet(f"color: {ERROR};")
            self.resolved_edit.setToolTip(message or "")
        self._update_open_buttons()

    def _edit_settings(self) -> None:
        plat = current_platform()
        dlg = QDialog(self)
        dlg.setWindowTitle("Shot2EXR Settings")
        dlg.resize(640, 220)
        form = QFormLayout(dlg)
        root_edit = QLineEdit(self._settings.projects_root(plat))
        root_edit.setPlaceholderText("Production storage root for this operating system")
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: root_edit.setText(
            QFileDialog.getExistingDirectory(dlg, "Projects root", root_edit.text()) or root_edit.text()))
        row = QHBoxLayout()
        row.addWidget(root_edit, 1)
        row.addWidget(browse)
        form.addRow(f"Projects root ({plat})", row)
        note = QLabel(f"Saved to {self._settings.user_file}.\nProject and task folder mappings "
                      "([projects], [tasks]) are edited in that file.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow("", note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        self._settings.roots[plat] = root_edit.text().strip()
        try:
            path = save_settings(self._settings)
        except OSError as exc:
            self._log(f"ERROR: cannot save settings: {exc}")
            return
        self._log(f"Settings saved to {path}")
        self._load_studio_settings()
        self._update_preview()

    # ------------------------------------------------------------------ drag and drop

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.set_input_path(urls[0].toLocalFile())
            event.acceptProposedAction()
