"""Settings remembered per project + shot and reused by the CLI and the GUI."""

import json
import os
import time
from pathlib import Path

import pytest

from shot2exr import shot_memory
from shot2exr.cli import main
from shot2exr.errors import ExitCode
from shot2exr.models import ConversionRequest, Resolution, ResizeMode

pytestmark = pytest.mark.usefixtures("ffmpeg_bin")


def _cli(src, out, *extra, task="comp", version="001"):
    return ["--input", str(src), "--project", "PROJ", "--shot", "0010_020", "--task", task, "--element", "water",
            "--version", version, "--start-frame", "1009", "--output-dir", str(out), *extra]


def test_history_path_and_round_trip(tmp_path, monkeypatch):
    assert shot_memory.history_path() == tmp_path / "shot_history.json"  # conftest isolates it
    monkeypatch.delenv(shot_memory.HISTORY_ENV)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    expected = (tmp_path / "appdata" / "Shot2EXR" if os.name == "nt" else tmp_path / "xdg" / "shot2exr") / "shot_history.json"
    assert shot_memory.history_path() == expected

    req = ConversionRequest(input_path=Path("x"), project="PROJ", shot="0010_020", task="comp", version="v001",
                            start_frame=1001, output_resolution=Resolution(1920, 1080), element="water",
                            input_colorspace="sRGB Encoded Rec.709 (sRGB)", output_colorspace="ACEScg",
                            resize_mode=ResizeMode.FILL)
    assert shot_memory.remember(req) == expected
    memo = shot_memory.recall("PROJ", "0010_020")
    assert memo["resolution"] == "1920x1080" and memo["resize_mode"] == "fill" and memo["task"] == "comp"
    assert memo["input_colorspace"] == "sRGB Encoded Rec.709 (sRGB)" and "ocio_config" not in memo
    for never in ("element", "version", "start_frame"):
        assert never not in json.loads(expected.read_text())["shots"]["PROJ/0010_020"]
    assert shot_memory.recall("PROJ", "0010_030") is None and shot_memory.recall("", "0010_020") is None


def test_damaged_or_unwritable_history_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "shot_history.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(shot_memory.HistoryError, match="damaged"):
        shot_memory.recall("PROJ", "0010_020")
    req = ConversionRequest(input_path=Path("x"), project="PROJ", shot="0010_020", task="comp", version="v001",
                            start_frame=1001, output_resolution=Resolution(64, 36), element="water")
    assert shot_memory.remember(req) == path  # a damaged file is replaced
    assert shot_memory.recall("PROJ", "0010_020")["resolution"] == "64x36"
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(shot_memory.HISTORY_ENV, str(blocker / "sub" / "history.json"))  # parent is a file
    with pytest.raises(shot_memory.HistoryError, match="Cannot save"):
        shot_memory.remember(req)


def test_key_ignores_case_and_spaces(tmp_path):
    req = ConversionRequest(input_path=Path("x"), project="proj", shot="0010_abc", task="comp", version="v001",
                            start_frame=1001, output_resolution=Resolution(64, 36), element="water")
    shot_memory.remember(req)
    assert shot_memory.recall(" PROJ ", "0010_ABC")["resolution"] == "64x36"
    # Entries written by the first version (key as typed) are still found.
    (tmp_path / "shot_history.json").write_text(json.dumps({"shots": {"Proj/0010_x": {"resolution": "8x8"}}}))
    assert shot_memory.recall("PROJ", "0010_X")["resolution"] == "8x8"


def test_cli_remembers_and_reuses_for_another_task(make_exr_sequence, tmp_path, capsys):
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    assert main(_cli(src, tmp_path / "a", "--resolution", "64x36", "--resize-mode", "fill",
                     "--output-colorspace", "ACES2065-1")) == ExitCode.OK
    capsys.readouterr()

    # Another task of the same shot: no resolution or colour flags needed.
    assert main(_cli(src, tmp_path / "b", "--dry-run", "--json", task="paint")) == ExitCode.OK
    captured = capsys.readouterr()
    plan = json.loads(captured.out)
    assert plan["output"]["resolution"] == "64x36" and plan["output"]["resize_mode"] == "fill"
    assert plan["color"]["output_colorspace"] == "ACES2065-1"
    assert "remembered for PROJ/0010_020" in captured.err

    # Explicit options win; --no-recall ignores the memory.
    assert main(_cli(src, tmp_path / "b", "--dry-run", "--json", "--resolution", "32x18", task="paint")) == ExitCode.OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["output"]["resolution"] == "32x18" and plan["color"]["output_colorspace"] == "ACES2065-1"
    assert main(_cli(src, tmp_path / "b", "--dry-run", "--no-recall")) == ExitCode.USAGE  # resolution required again
    assert "--resolution" in capsys.readouterr().err


def test_failed_conversion_is_not_remembered(make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    assert main(_cli(src, tmp_path / "a", "--resolution", "64x36", "--dry-run")) == ExitCode.OK
    assert shot_memory.recall("PROJ", "0010_020") is None  # dry runs never save


def _wait(app, w, timeout=60):
    end = time.monotonic() + timeout
    while w._tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _log(w):
    return w.log.toPlainText()


def test_gui_real_flow_saves_then_loads_in_a_new_window(make_exr_sequence, tmp_path, isolated_settings):
    """What an artist does: type the shot, set format and colour, convert; restart, type the shot, another task."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QTest = pytest.importorskip("PySide6.QtTest").QTest
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from shot2exr.gui.main_window import MainWindow

    root = tmp_path / "Projects"
    root.mkdir()
    isolated_settings.write_text(f'[paths.linux]\nprojects_root = "{root.as_posix()}"\n[paths.windows]\n'
                                 f'projects_root = "{root.as_posix()}"\n[projects]\nPROJ = "MyProject"\n'
                                 '[tasks]\ncomp = "Compositing"\npaint = "Paint"\n', encoding="utf-8")
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})

    w = MainWindow()
    assert str(shot_memory.history_path()) in _log(w)  # where the file lives is shown at start-up
    w.set_input_path(str(src))
    _wait(app, w)
    for edit, text in ((w.project_edit, "PROJ"), (w.shot_edit, "0010_020"), (w.task_edit, "comp"),
                       (w.element_edit, "water")):
        QTest.keyClicks(edit, text)
    w.width_spin.setValue(48)
    w.height_spin.setValue(27)
    w.resize_combo.setCurrentIndex(w.resize_combo.findData(ResizeMode.STRETCH))
    w.output_cs.setCurrentText("ACES2065-1")
    w.dry_run()
    _wait(app, w)
    assert not shot_memory.history_path().exists()  # a dry run saves nothing (and says so)
    assert "dry run saves nothing" in _log(w)
    w.start_conversion()
    _wait(app, w)
    assert "Conversion complete" in w.result_label.text()
    assert f"in {shot_memory.history_path()}" in _log(w)
    w.close()

    w = MainWindow()  # a new session, settings typed by hand before the shot is known are replaced
    w.width_spin.setValue(1000)
    QTest.keyClicks(w.project_edit, "proj")  # case does not matter
    QTest.keyClicks(w.shot_edit, "0010_020")
    QTest.keyClicks(w.task_edit, "paint")
    assert (w.width_spin.value(), w.height_spin.value()) == (48, 27)
    assert w.output_cs.currentText() == "ACES2065-1" and w.resize_combo.currentData() == ResizeMode.STRETCH
    assert "Loaded the settings remembered for PROJ/0010_020" in w.recall_label.text()
    w.width_spin.setValue(64)  # an edit after loading is kept while the shot stays the same
    QTest.keyClicks(w.task_edit, "x")
    assert w.width_spin.value() == 64
    w.shot_edit.setText("0010_030")  # nothing remembered: fields stay as they are
    assert w.recall_label.text() == "" and w.width_spin.value() == 64
    w.close()


def test_gui_shows_history_problems(make_exr_sequence, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from shot2exr.gui.main_window import MainWindow

    shot_memory.history_path().write_text("{broken", encoding="utf-8")
    w = MainWindow()
    w.project_edit.setText("PROJ")
    w.shot_edit.setText("0010_020")
    assert "damaged" in w.recall_label.text() and "damaged" in _log(w)

    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(shot_memory.HISTORY_ENV, str(blocker / "history.json"))
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    for edit, text in ((w.task_edit, "comp"), (w.element_edit, "water"), (w.output_edit, str(tmp_path / "gui")),
                       (w.input_edit, str(src))):
        edit.setText(text)
    w.manual_check.setChecked(True)
    w.start_conversion()
    _wait(app, w)
    assert "Conversion complete" in w.result_label.text()  # the EXRs are fine; only the memory failed
    assert "Cannot save remembered settings" in _log(w)
    w.close()


def test_cli_reports_save_location_and_failures(make_exr_sequence, tmp_path, capsys, monkeypatch):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    assert main(_cli(src, tmp_path / "a", "--resolution", "64x36")) == ExitCode.OK
    assert f"Settings remembered for PROJ/0010_020 in {shot_memory.history_path()}" in capsys.readouterr().out
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(shot_memory.HISTORY_ENV, str(blocker / "history.json"))
    assert main(_cli(src, tmp_path / "b", "--resolution", "64x36")) == ExitCode.OK
    assert "Cannot save remembered settings" in capsys.readouterr().err
