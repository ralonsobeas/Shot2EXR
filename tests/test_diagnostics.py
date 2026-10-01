"""Native dependency checks (``shot2exr --check-environment``)."""

import json

import pytest

from shot2exr import cli, diagnostics
from shot2exr.errors import ExitCode

pytest.importorskip("OpenImageIO")
pytest.importorskip("PyOpenColorIO")


def test_core_dependencies_pass(ffmpeg_bin):
    checks = {c.name: c for c in diagnostics.run_checks(include_gui=False)}
    for name in ("Python", "NumPy", "OpenImageIO", "OpenColorIO", "FFmpeg", "FFprobe"):
        assert checks[name].status == diagnostics.OK, checks[name]
    assert "16-bit RGB decode OK" in checks["FFmpeg"].detail


def test_missing_ffmpeg_fails_with_dependency_code(tmp_path, capsys):
    code = cli.main(["--check-environment", "--json", "--ffmpeg-path", str(tmp_path / "nope" / "ffmpeg")])
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.DEPENDENCY and payload["ok"] is False and "FFmpeg" in payload["failed"]


def test_bad_ocio_config_is_reported(tmp_path):
    bad = tmp_path / "broken.ocio"
    bad.write_text("not: [an ocio config")
    check = diagnostics.check_opencolorio(str(bad))
    assert check.status == diagnostics.FAIL and "Invalid OCIO config" in check.detail


def test_unset_projects_root_is_a_warning_not_a_failure():
    check = diagnostics.check_settings()
    assert check.status == diagnostics.WARN and "projects_root" in check.detail


def test_text_report_lists_every_check(ffmpeg_bin):
    text = diagnostics.format_checks(diagnostics.run_checks(include_gui=False))
    assert text.splitlines()[0].endswith("environment check") and "RESULT:" in text


def test_bundled_ffmpeg_is_preferred_when_frozen(tmp_path, monkeypatch):
    import os
    import sys

    from shot2exr import media_probe

    exe = tmp_path / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert media_probe.resolve_executable("ffmpeg") == exe
