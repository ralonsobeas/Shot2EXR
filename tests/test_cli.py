import json

from shot2exr.cli import main
from shot2exr.errors import ExitCode


def _args(src, out, *extra):
    return ["--input", str(src), "--project", "GOD", "--shot", "0046_005", "--task", "ml", "--element", "water", "--version", "001",
            "--start-frame", "1009", "--resolution", "2048x1152", "--input-colorspace", "auto",
            "--output-colorspace", "ACEScg", "--output-dir", str(out), *extra]


def test_version_flag(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "Shot2EXR" in capsys.readouterr().out


def test_spec_example_dry_run_json(make_exr_sequence, tmp_path, capsys):
    src = make_exr_sequence(range(1001, 1005), attrs={"colorInteropID": "lin_ap1_scene"})
    code = main(_args(src, tmp_path, "--dry-run", "--json", "--ocio-config", "ocio://studio-config-latest"))
    data = json.loads(capsys.readouterr().out)
    assert code == ExitCode.OK and data["ok"]
    assert data["output"]["filenames_preview"][0] == "GOD_0046_005_ml_v001.1009.exr"
    assert data["output"]["end_frame"] == 1012


def test_inferred_needs_flag_in_cli(make_video, tmp_path, capsys):
    src = make_video("a b.mov", frames=5)
    assert main(_args(src, tmp_path, "--dry-run")) == ExitCode.COLORSPACE
    assert "--accept-inferred-colorspace" in capsys.readouterr().out
    assert main(_args(src, tmp_path, "--dry-run", "--accept-inferred-colorspace")) == ExitCode.OK


def test_conversion_not_implemented_yet(make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    assert main(_args(src, tmp_path)) == ExitCode.NOT_IMPLEMENTED
    assert not any(p.suffix == ".exr" for p in tmp_path.iterdir())


def test_inspect_and_errors(make_exr_sequence, tmp_path, capsys):
    src = make_exr_sequence([1, 2])
    assert main(["--inspect", "--input", str(src)]) == ExitCode.OK
    assert "UNKNOWN" in capsys.readouterr().out
    assert main(["--input", str(src), "--dry-run"]) == ExitCode.USAGE  # missing parameters
    assert main(["--inspect", "--input", str(tmp_path / "nope.mov")]) == ExitCode.INPUT
    assert main(_args(src, tmp_path, "--dry-run", "--resolution", "big")) == ExitCode.USAGE
    assert main(_args(src, tmp_path, "--dry-run", "--ocio-config", str(tmp_path / "x.ocio"))) == ExitCode.COLORSPACE


def test_list_colorspaces(capsys):
    assert main(["--list-colorspaces"]) == ExitCode.OK
    assert "ACEScg" in capsys.readouterr().out.splitlines()
