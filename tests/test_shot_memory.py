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


def test_damaged_or_unwritable_history_is_harmless(tmp_path, monkeypatch):
    path = tmp_path / "shot_history.json"
    path.write_text("{not json", encoding="utf-8")
    assert shot_memory.recall("PROJ", "0010_020") is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(shot_memory.HISTORY_ENV, str(blocker / "sub" / "history.json"))  # parent is a file
    req = ConversionRequest(input_path=Path("x"), project="PROJ", shot="0010_020", task="comp", version="v001",
                            start_frame=1001, output_resolution=Resolution(64, 36), element="water")
    assert shot_memory.remember(req) is None


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


def test_gui_prefills_remembered_settings(make_exr_sequence, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from shot2exr.gui.main_window import MainWindow

    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    assert main(_cli(src, tmp_path / "a", "--resolution", "64x36", "--resize-mode", "stretch",
                     "--output-colorspace", "ACES2065-1")) == ExitCode.OK

    w = MainWindow()
    w.width_spin.setValue(100)  # changed by hand before the shot is known: kept
    w.project_edit.setText("PROJ")
    w.shot_edit.setText("0010_020")
    assert w.output_cs.currentText() == "ACES2065-1" and w.resize_combo.currentData() == ResizeMode.STRETCH
    assert w.width_spin.value() == 100
    assert "remembered for PROJ/0010_020" in w.recall_label.text()

    w.shot_edit.setText("0010_030")  # nothing remembered: fields stay as they are
    assert w.recall_label.text() == "" and w.output_cs.currentText() == "ACES2065-1"
    w.shot_edit.setText("0010_020")
    assert w.width_spin.value() == 64  # the earlier hand edit was consumed by the first recall

    # A GUI conversion is remembered too.
    w.task_edit.setText("comp")
    w.element_edit.setText("water")
    w.version_edit.setText("002")
    w.output_edit.setText(str(tmp_path / "gui"))
    w.manual_check.setChecked(True)
    w.input_edit.setText(str(src))
    w.width_spin.setValue(48)
    w.height_spin.setValue(27)
    w.start_conversion()
    end = time.monotonic() + 30
    while w._tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert "Conversion complete" in w.result_label.text()
    assert shot_memory.recall("PROJ", "0010_020")["resolution"] == "48x27"
    w.close()
