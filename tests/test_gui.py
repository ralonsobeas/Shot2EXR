"""Headless GUI smoke tests (QT_QPA_PLATFORM=offscreen)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from shot2exr.gui.styles import STYLESHEET

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setOrganizationName("Shot2EXR-tests")
    a.setStyleSheet(STYLESHEET)
    return a


def _wait(app, window, timeout=20):
    end = time.monotonic() + timeout
    while window._tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not window._tasks, "worker did not finish"


def test_window_launches_and_previews(app):
    from shot2exr.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    assert w.output_cs.currentText() == "ACEScg" and w.input_cs.currentText() == "auto"
    assert w.start_btn.isEnabled() and not w.cancel_btn.isEnabled()
    w.project_edit.setText("GOD")
    w.shot_edit.setText("0046_005")
    w.task_edit.setText("ml")
    w.version_edit.setText("v1")
    w.start_spin.setValue(1009)
    assert w.preview_label.text() == "GOD_0046_005_ml_v001.1009.exr"
    w.shot_edit.setText("../x")
    assert "invalid" in w.preview_label.text()
    w.close()


def test_inspect_and_dry_run_in_worker(app, make_exr_sequence, tmp_path):
    from shot2exr.gui.main_window import MainWindow

    src = make_exr_sequence(range(1001, 1004), attrs={"colorInteropID": "lin_ap1_scene"})
    w = MainWindow()
    for edit, text in ((w.project_edit, "GOD"), (w.shot_edit, "0046_005"), (w.task_edit, "ml"),
                       (w.element_edit, "water"), (w.output_edit, str(tmp_path))):
        edit.setText(text)
    w.manual_check.setChecked(True)
    w.set_input_path(str(src))
    _wait(app, w)
    assert w.src_labels["Frames"].text() == "3" and w.src_labels["Detection"].text() == "DETECTED"
    assert w.range_label.text().startswith("1001-1003")
    w.dry_run()
    _wait(app, w)
    assert "Dry run OK" in w.result_label.text()
    assert "RESULT: OK" in w.log.toPlainText()
    w.close()


def test_automatic_output_directory_preview(app, isolated_settings, tmp_path):
    from shot2exr.gui.main_window import MainWindow

    isolated_settings.write_text(f'[paths.linux]\nprojects_root = "{tmp_path.as_posix()}"\n'
                                 f'[paths.windows]\nprojects_root = "{tmp_path.as_posix()}"\n')
    w = MainWindow()
    for edit, text in ((w.project_edit, "GOD"), (w.shot_edit, "0046_005"), (w.task_edit, "ml"),
                       (w.element_edit, "water"), (w.version_edit, "1")):
        edit.setText(text)
    expected = tmp_path / "GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water/GOD_0046_005_ml_v001"
    assert w.resolved_edit.isReadOnly() and w.resolved_edit.text() == str(expected)
    w.element_edit.setText("../fire")
    assert "Element" in w.resolved_edit.text()
    w.element_edit.setText("fire")
    w.output_edit.setText(str(tmp_path / "manual"))
    assert w.resolved_edit.text().endswith("fire" + __import__("os").sep + "GOD_0046_005_ml_v001")  # override off
    w.manual_check.setChecked(True)
    assert w.resolved_edit.text() == str(tmp_path / "manual")
    w.close()


def test_conversion_in_worker(app, make_exr_sequence, tmp_path):
    from shot2exr.gui.main_window import MainWindow

    src = make_exr_sequence([1001, 1002, 1003], attrs={"colorInteropID": "lin_ap1_scene"})
    out = tmp_path / "out"
    w = MainWindow()
    for edit, text in ((w.project_edit, "GOD"), (w.shot_edit, "0046_005"), (w.task_edit, "ml"),
                       (w.element_edit, "water"), (w.output_edit, str(out)), (w.input_edit, str(src))):
        edit.setText(text)
    w.manual_check.setChecked(True)
    w.width_spin.setValue(64)
    w.height_spin.setValue(36)
    w.start_conversion()
    _wait(app, w)
    assert "Conversion complete: 3 frames" in w.result_label.text()
    assert w.progress.value() == 3 and w.frame_label.text() == "3 / 3"
    assert w.open_report_btn.isEnabled() and not w.cancel_btn.isEnabled()
    assert (out / "GOD_0046_005_ml_v001.conversion_report.json").is_file()
    w.close()
