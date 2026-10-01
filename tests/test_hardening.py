"""Milestone 4 hardening: OS failures, failure-report fallbacks, disk-space preflight, bounded memory."""

import errno
import tracemalloc
from collections import namedtuple
from pathlib import Path

import pytest

from shot2exr import cli, converter
from shot2exr.converter import plan_conversion, run_conversion
from shot2exr.errors import ExitCode, describe_os_error
from shot2exr.models import ConversionRequest, Resolution

np = pytest.importorskip("numpy")
from test_exr_conversion import convert, write_exr  # noqa: E402


@pytest.fixture
def seq(tmp_path):
    def _make(n=3, size=(48, 27)):
        d = tmp_path / f"src{n}"
        d.mkdir()
        rng = np.random.default_rng(n)
        for i in range(n):
            write_exr(d / f"plate.{1001 + i}.exr", rng.random((size[1], size[0], 3), dtype=np.float32),
                      {"colorInteropID": "lin_ap1_scene"})
        return d
    return _make


def test_describe_os_error_is_readable():
    msg = describe_os_error(OSError(errno.ENOSPC, "No space", "/out/a.exr"), "Writing the output")
    assert msg == "Writing the output failed: no space left on the device (/out/a.exr)"
    assert "permission denied" in describe_os_error(PermissionError(errno.EACCES, "x"), "Writing")


def test_disk_full_during_write_fails_with_output_code(ocio_cfg, seq, tmp_path, monkeypatch):
    def full(path, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device", str(path))

    monkeypatch.setattr(converter, "write_frame", full)
    out = tmp_path / "out"
    _, res = convert(ocio_cfg, seq(3), out)
    assert res.status == "failed" and res.exit_code is ExitCode.OUTPUT
    assert "no space left on the device" in res.errors[0]
    assert [p.name for p in out.iterdir()] == ["PROJ_0010_020_comp_v001.conversion_report.FAILED.json"]


def test_unwritable_failure_report_does_not_crash(ocio_cfg, seq, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("decoder exploded")

    def no_report(path, data):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(converter, "write_frame", boom)
    monkeypatch.setattr(converter, "write_report", no_report)
    _, res = convert(ocio_cfg, seq(2), tmp_path / "out")
    assert res.status == "failed" and res.report_path is None
    assert "decoder exploded" in res.errors[0] and "permission denied" in res.errors[1]


def test_low_disk_space_warns_in_plan(ocio_cfg, seq, tmp_path, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(converter.shutil, "disk_usage", lambda p: usage(10, 10, 1000))
    req = ConversionRequest(input_path=seq(2), project="PROJ", shot="0010_020", task="comp", version="001",
                            start_frame=1009, output_resolution=Resolution(2048, 1152), element="water",
                            output_directory=tmp_path / "out")
    plan = plan_conversion(req, ocio_cfg)
    assert plan.ok and any("GB free on the output storage" in w for w in plan.warnings)


def test_memory_does_not_grow_with_sequence_length(ocio_cfg, seq, tmp_path):
    """Frames are streamed: converting 10x more frames must not need ~10x more memory."""
    def peak(n):
        src = seq(n, size=(256, 144))
        tracemalloc.start()
        _, res = convert(ocio_cfg, src, tmp_path / f"out{n}", output_resolution=Resolution(320, 180))
        _, top = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert res.ok
        return top

    short, long = peak(3), peak(30)
    assert long < short * 1.5, (short, long)


def test_cli_unexpected_error_is_reported_not_traceback(monkeypatch, tmp_path, capsys, seq):
    def explode(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(cli, "plan_conversion", explode)
    code = cli.main(["-i", str(seq(1)), "--project", "PROJ", "--shot", "0010_020", "--task", "comp", "--element", "w",
                     "--version", "001", "--start-frame", "1001", "--resolution", "48x27", "-o", str(tmp_path / "o")])
    err = capsys.readouterr().err
    assert code == ExitCode.ERROR and "internal error: RuntimeError: something unexpected" in err
    assert "Traceback" not in err


def test_failed_cli_conversion_returns_specific_exit_code(monkeypatch, tmp_path, capsys, seq):
    def full(path, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device", str(path))

    monkeypatch.setattr(converter, "write_frame", full)
    code = cli.main(["-i", str(seq(2)), "--project", "PROJ", "--shot", "0010_020", "--task", "comp", "--element", "w",
                     "--version", "001", "--start-frame", "1001", "--resolution", "48x27", "-o", str(tmp_path / "o")])
    assert code == ExitCode.OUTPUT and "no space left" in capsys.readouterr().err
