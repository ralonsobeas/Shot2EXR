"""Automatic output directory structure (shared builder used by CLI, GUI and converter)."""

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from shot2exr.converter import plan_conversion
from shot2exr.errors import ConfigError, ExitCode, ValidationError
from shot2exr.models import ConversionRequest, Resolution
from shot2exr.output_paths import resolve_output_location, sequence_from_shot
from shot2exr.settings import Settings, load_settings, save_settings

WIN_EXPECTED = r"T:\Volumes\Projects\GodOfTides\VFX\GOD_0046\GOD_0046_005\Tasks\MachineLearning\ComfyUI\water\GOD_0046_005_ml_v001"


def _loc(settings, platform, **kw):
    args = dict(project="GOD", shot="0046_005", task="ml", element="water", version="001")
    args.update(kw)
    return resolve_output_location(settings=settings, platform=platform, **args)


@pytest.fixture
def settings():
    s = load_settings()  # bundled defaults (user file isolated by conftest)
    s.roots["linux"] = "/mnt/prod/projects"
    return s


def test_defaults_ship_initial_mappings_and_empty_linux_root():
    s = load_settings()
    assert s.projects == {"GOD": "GodOfTides"} and s.tasks == {"ml": "MachineLearning"}
    assert s.projects_root("windows") == "T:/Volumes/Projects" and s.projects_root("linux") == ""


@pytest.mark.parametrize("shot,seq", [("0046_005", "0046"), ("0001_010", "0001"), ("0046", "0046"), ("A10_020_b", "A10")])
def test_sequence_from_shot_keeps_leading_zeros(shot, seq):
    assert sequence_from_shot(shot) == seq


def test_windows_path_construction(settings):
    loc = _loc(settings, "windows")
    assert isinstance(loc.directory, PureWindowsPath) and str(loc.directory) == WIN_EXPECTED
    assert loc.origin == "auto" and loc.element == "water"


def test_rocky_linux_path_construction(settings):
    loc = _loc(settings, "linux", version="v12", element="smoke")
    assert loc.directory == PurePosixPath(
        "/mnt/prod/projects/GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/smoke/GOD_0046_005_ml_v012")


def test_empty_linux_root_is_a_clear_config_error():
    with pytest.raises(ConfigError, match=r"\[paths.linux\] projects_root"):
        _loc(load_settings(), "linux")


def test_projects_root_override_and_relative_root(settings):
    assert str(_loc(settings, "linux", projects_root="/other").directory).startswith("/other/GodOfTides/")
    with pytest.raises(ConfigError):
        _loc(settings, "linux", projects_root="relative/root")


def test_unmapped_codes(settings):
    with pytest.raises(ConfigError, match="project code 'XYZ'"):
        _loc(settings, "linux", project="XYZ")
    with pytest.raises(ConfigError, match=r"\[tasks\]"):
        _loc(settings, "linux", task="comp")


@pytest.mark.parametrize("element", ["", "../water", "a/b", "a\\b", "fire smoke", ".."])
def test_element_validation(settings, element):
    with pytest.raises(ValidationError):
        _loc(settings, "linux", element=element)


def test_manual_override_wins(settings, tmp_path):
    loc = _loc(load_settings(), "linux", manual_directory=tmp_path / "x")  # no root needed
    assert loc.origin == "manual" and loc.directory == tmp_path / "x"


def test_user_settings_file_adds_mappings(isolated_settings):
    isolated_settings.write_text('[paths.linux]\nprojects_root = "/prod"\n[projects]\nABC = "AlphaBetaCharlie"\n[tasks]\ncomp = "Compositing"\n')
    s = load_settings()
    assert s.projects == {"GOD": "GodOfTides", "ABC": "AlphaBetaCharlie"} and s.tasks["comp"] == "Compositing"
    assert str(_loc(s, "linux", project="ABC", task="comp").directory).startswith("/prod/AlphaBetaCharlie/VFX/ABC_0046/")


def test_save_settings_round_trip(isolated_settings):
    s = load_settings()
    s.roots["linux"] = '/prod/with "quotes"'
    save_settings(s)
    assert load_settings().projects_root("linux") == '/prod/with "quotes"'


# ------------------------------------------------------------------ dry run integration

def _req(src, **kw):
    base = dict(input_path=Path(src), project="GOD", shot="0046_005", task="ml", element="water", version="001",
                start_frame=1009, output_resolution=Resolution(2048, 1152), dry_run=True)
    base.update(kw)
    return ConversionRequest(**base)


@pytest.fixture
def exr_src(make_exr_sequence):
    return make_exr_sequence([1001, 1002], attrs={"colorInteropID": "lin_ap1_scene"})


def test_dry_run_auto_directory_creates_nothing(ocio_cfg, exr_src, tmp_path):
    root = tmp_path / "Projects"
    root.mkdir()
    plan = plan_conversion(_req(exr_src, projects_root=str(root)), ocio_cfg, settings=load_settings())
    assert plan.ok, plan.errors
    expected = root / "GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water/GOD_0046_005_ml_v001"
    assert Path(plan.location.directory) == expected
    d = plan.to_dict()["output"]
    assert d["directory"] == str(expected) and d["element"] == "water" and d["directory_origin"] == "auto"
    assert list(root.iterdir()) == []  # dry run never creates directories


def test_dry_run_reports_unmounted_root(ocio_cfg, exr_src, tmp_path):
    plan = plan_conversion(_req(exr_src, projects_root=str(tmp_path / "not_mounted")), ocio_cfg, settings=load_settings())
    assert plan.exit_code is ExitCode.OUTPUT and "not mounted" in plan.errors[0].message


def test_dry_run_missing_linux_root_is_config_error(ocio_cfg, exr_src):
    plan = plan_conversion(_req(exr_src), ocio_cfg, settings=Settings(roots={"linux": "", "windows": ""},
                                                                     projects={"GOD": "GodOfTides"}, tasks={"ml": "MachineLearning"}))
    assert plan.exit_code is ExitCode.CONFIG and plan.location is None


def test_existing_version_directory_and_collisions(ocio_cfg, exr_src, tmp_path):
    root = tmp_path / "Projects"
    vdir = root / "GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water/GOD_0046_005_ml_v001"
    vdir.mkdir(parents=True)
    plan = plan_conversion(_req(exr_src, projects_root=str(root)), ocio_cfg, settings=load_settings())
    assert plan.ok  # empty existing version directory is fine
    (vdir / "notes.txt").write_text("x")
    plan = plan_conversion(_req(exr_src, projects_root=str(root)), ocio_cfg, settings=load_settings())
    assert plan.exit_code is ExitCode.OUTPUT and "not empty" in plan.errors[0].message
    (vdir / "GOD_0046_005_ml_v001.1009.exr").write_bytes(b"")
    plan = plan_conversion(_req(exr_src, projects_root=str(root)), ocio_cfg, settings=load_settings())
    assert plan.collisions == ["GOD_0046_005_ml_v001.1009.exr"] and not plan.ok
    plan = plan_conversion(_req(exr_src, projects_root=str(root), overwrite=True), ocio_cfg, settings=load_settings())
    assert plan.ok


def test_explicit_output_dir_override(ocio_cfg, exr_src, tmp_path):
    plan = plan_conversion(_req(exr_src, output_directory=tmp_path / "manual"), ocio_cfg, settings=Settings())
    assert plan.ok and plan.location.origin == "manual" and Path(plan.location.directory) == tmp_path / "manual"
    assert not (tmp_path / "manual").exists()


def test_cli_auto_directory(exr_src, tmp_path, capsys):
    import json

    from shot2exr.cli import main

    root = tmp_path / "Projects"
    root.mkdir()
    args = ["--input", str(exr_src), "--project", "GOD", "--shot", "0046_005", "--task", "ml", "--element", "water",
            "--version", "001", "--start-frame", "1009", "--resolution", "2048x1152", "--input-colorspace", "auto",
            "--output-colorspace", "ACEScg", "--dry-run", "--json"]
    assert main(args + ["--projects-root", str(root)]) == ExitCode.OK
    out = json.loads(capsys.readouterr().out)["output"]
    assert out["directory"].endswith("ComfyUI/water/GOD_0046_005_ml_v001".replace("/", __import__("os").sep))
    assert main(args) == ExitCode.CONFIG  # Linux root empty by default
    assert main(args[:8] + args[10:]) == ExitCode.USAGE  # --element missing
    assert list(root.iterdir()) == []
