"""End-to-end: the installed ``shot2exr`` / ``shot2exr-gui`` commands run as separate processes.

Set ``SHOT2EXR_CLI`` / ``SHOT2EXR_GUI`` to test a standalone bundle instead of the installed
entry points (``packaging/build.py --test`` does this).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from shot2exr.exr_reader import read_header

pytest.importorskip("OpenImageIO")
BASE = "GOD_0046_005_ml_v003"


def _command(env_var: str, name: str, module: str) -> list[str]:
    if os.environ.get(env_var):
        return [os.environ[env_var]]
    exe = shutil.which(name)
    return [exe] if exe else [sys.executable, "-m", module]


CLI = _command("SHOT2EXR_CLI", "shot2exr", "shot2exr")
GUI = _command("SHOT2EXR_GUI", "shot2exr-gui", "shot2exr.gui")


def run(*args, env=None, timeout=300):
    return subprocess.run([*CLI, *map(str, args)], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


@pytest.fixture
def projects(tmp_path, isolated_settings):
    root = tmp_path / "Projects"
    root.mkdir()
    isolated_settings.write_text(
        f'[paths.linux]\nprojects_root = "{root.as_posix()}"\n[paths.windows]\nprojects_root = "{root.as_posix()}"\n'
        '[projects]\nGOD = "GodOfTides"\n[tasks]\nml = "MachineLearning"\n')
    return root


SHOT = ["--project", "GOD", "--shot", "0046_005", "--task", "ml", "--element", "water", "--version", "003",
        "--start-frame", "1001"]


def test_version_and_help():
    assert run("--version").stdout.strip().startswith("Shot2EXR ")
    assert "--check-environment" in run("--help").stdout


def test_environment_check_runs(ffmpeg_bin):
    res = run("--check-environment", "--json")
    payload = json.loads(res.stdout)
    core = {c["name"]: c["status"] for c in payload["checks"]}
    assert all(core[n] == "ok" for n in ("OpenImageIO", "OpenColorIO", "FFmpeg", "FFprobe")), payload


def test_exr_sequence_end_to_end(projects, make_exr_sequence):
    src = make_exr_sequence(range(1, 5), attrs={"colorInteropID": "lin_ap0_scene"}, size=(64, 36))
    res = run("-i", src, *SHOT, "--resolution", "96x54", "--resize-mode", "fit")
    assert res.returncode == 0, res.stderr
    vdir = projects / "GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water" / BASE
    frames = sorted(p.name for p in vdir.glob("*.exr"))
    assert frames == [f"{BASE}.{n}.exr" for n in range(1001, 1005)]
    hdr = read_header(vdir / frames[0])
    assert (hdr.width, hdr.height) == (96, 54) and hdr.attributes.get("colorInteropID") == "lin_ap1_scene"
    report = json.loads((vdir / f"{BASE}.conversion_report.json").read_text())
    assert report["general"]["status"] == "success" and report["output"]["exported_frame_count"] == 4
    assert not [p for p in vdir.parent.iterdir() if p.name.startswith(".")]  # no staging left behind

    again = run("-i", src, *SHOT, "--resolution", "96x54")  # same version again: refused, nothing touched
    assert again.returncode == 5 and "already exist" in again.stdout + again.stderr


def test_video_end_to_end(projects, make_video):
    src = make_video("plate.mov", frames=12)
    unconfirmed = run("-i", src, *SHOT, "--resolution", "160x90")
    assert unconfirmed.returncode == 4  # INFERRED colour space needs confirmation
    res = run("-i", src, *SHOT, "--resolution", "160x90", "--accept-inferred-colorspace", "--json")
    assert res.returncode == 0, res.stderr
    result = json.loads(res.stdout)["result"]
    vdir = Path(result["output_directory"])
    assert result["frames_written"] == 12 and len(list(vdir.glob(f"{BASE}.*.exr"))) == 12
    report = json.loads(Path(result["report"]).read_text())
    assert report["input"]["frames_decoded"] == 12 and "ffmpeg" in report["color_management"]["decoder_command"][0]


def test_error_exit_codes(projects, tmp_path):
    assert run("-i", tmp_path / "missing.mov", *SHOT, "--resolution", "64x36").returncode == 3
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    assert run("-i", bad, *SHOT, "--resolution", "64x36").returncode == 3
    assert run("-i", bad, *SHOT, "--resolution", "64").returncode == 2
    assert run("-i", bad, "--project", "GOD").returncode == 2


def test_gui_self_test_process():
    env = {**os.environ}
    if sys.platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    res = subprocess.run([*GUI, "--self-test"], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr + res.stdout
