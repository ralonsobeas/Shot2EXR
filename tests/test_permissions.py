"""A folder the user cannot open on the output storage (e.g. a mount with restrictive permissions).

Python's ``Path.exists()`` raises ``PermissionError`` there instead of returning False; the engine,
CLI and GUI must report it as a normal output problem naming the folder, never as a traceback.
"""

import errno
import os
import sys
from pathlib import Path

import pytest

from shot2exr.converter import blocked_ancestor, plan_conversion
from shot2exr.errors import ExitCode
from shot2exr.models import ConversionRequest, Resolution
from shot2exr.settings import load_settings

VERSION_DIR = "Alpha/VFX/ABC_0010/ABC_0010_020/Tasks/Task/ComfyUI/fx/ABC_0010_020_tsk_v001"


@pytest.fixture
def locked(tmp_path, isolated_settings, monkeypatch):
    """Projects root with a 'Alpha/VFX' folder whose contents this user may not open."""
    root = tmp_path / "Projects"
    (root / "Alpha" / "VFX").mkdir(parents=True)
    isolated_settings.write_text(
        f'[paths.linux]\nprojects_root = "{root.as_posix()}"\n[paths.windows]\nprojects_root = "{root.as_posix()}"\n'
        '[projects]\nABC = "Alpha"\n[tasks]\ntsk = "Task"\n', encoding="utf-8")
    blocked = root / "Alpha" / "VFX"
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if blocked in Path(self).parents:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    return root, blocked


def request(src, **kw):
    params = dict(input_path=Path(src), project="ABC", shot="0010_020", task="tsk", element="fx", version="001",
                  start_frame=1001, output_resolution=Resolution(64, 36))
    params.update(kw)
    return ConversionRequest(**params)


def test_blocked_ancestor_names_the_folder(locked):
    root, blocked = locked
    assert blocked_ancestor(root / VERSION_DIR) == blocked
    assert blocked_ancestor(root / "Alpha" / "missing" / "x") is None


def test_plan_reports_unopenable_folder_instead_of_raising(locked, make_exr_sequence, ocio_cfg):
    root, blocked = locked
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(request(src), ocio_cfg, settings=load_settings())
    assert not plan.ok and plan.exit_code is ExitCode.OUTPUT
    msg = plan.errors[0].message
    assert f"the folder {blocked} cannot be opened by this user" in msg and str(root / VERSION_DIR) in msg


def test_cli_exit_code_and_no_traceback(locked, make_exr_sequence, capsys):
    from shot2exr.cli import main

    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    code = main(["-i", str(src), "--project", "ABC", "--shot", "0010_020", "--task", "tsk", "--element", "fx",
                 "--version", "001", "--start-frame", "1001", "--resolution", "64x36"])
    out = capsys.readouterr()
    assert code == ExitCode.OUTPUT
    assert "cannot be opened by this user" in out.out and "Traceback" not in out.out + out.err


def test_gui_dry_run_shows_the_error(locked, make_exr_sequence):
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from test_gui import _wait

    from PySide6.QtWidgets import QApplication

    from shot2exr.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    w = MainWindow()
    for edit, text in ((w.project_edit, "ABC"), (w.shot_edit, "0010_020"), (w.task_edit, "tsk"),
                       (w.element_edit, "fx"), (w.version_edit, "1")):
        edit.setText(text)
    w.set_input_path(str(src))
    _wait(app, w)
    w.dry_run()
    _wait(app, w)
    assert "cannot be opened by this user" in w.result_label.text()
    assert "Traceback" not in w.log.toPlainText()
    w.close()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs real POSIX permissions (root bypasses them)")
def test_real_permissions(tmp_path, isolated_settings, make_exr_sequence, ocio_cfg):
    root = tmp_path / "Projects"
    vfx = root / "Alpha" / "VFX"
    vfx.mkdir(parents=True)
    isolated_settings.write_text(f'[paths.linux]\nprojects_root = "{root.as_posix()}"\n'
                                 '[projects]\nABC = "Alpha"\n[tasks]\ntsk = "Task"\n', encoding="utf-8")
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    vfx.chmod(0o000)
    try:
        plan = plan_conversion(request(src), ocio_cfg, settings=load_settings())
    finally:
        vfx.chmod(0o755)
    assert plan.exit_code is ExitCode.OUTPUT and str(vfx) in plan.errors[0].message
