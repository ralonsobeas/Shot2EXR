"""Dry-run planning end to end (Milestone 1: nothing is written)."""

from pathlib import Path

import pytest

from conftest import AP1
from shot2exr.converter import inspect_source, plan_conversion
from shot2exr.errors import ExitCode, InputError, ValidationError
from shot2exr.models import ConversionRequest, Resolution


def _req(src, out, **kw):
    base = dict(input_path=Path(src), project="GOD", shot="0046_005", task="ml", version="001", start_frame=1009,
                output_resolution=Resolution(2048, 1152), element="water", output_directory=Path(out), dry_run=True)
    base.update(kw)
    return ConversionRequest(**base)


def test_exr_plan_ok(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence(range(1001, 1005), attrs={"colorInteropID": "lin_ap1_scene"})
    out = tmp_path / "out"
    out.mkdir()
    plan = plan_conversion(_req(src, out), ocio_cfg)
    assert plan.ok, plan.errors
    assert plan.frame_range == (1009, 1012)
    assert plan.output_filenames() == [f"GOD_0046_005_ml_v001.{f}.exr" for f in range(1009, 1013)]
    assert plan.input_colorspace == "ACEScg" and plan.input_colorspace_origin == "detected"
    assert plan.color_transforms == ["none (input and output are both 'ACEScg')"]
    assert list(out.iterdir()) == []  # dry run writes nothing
    d = plan.to_dict()
    assert d["output"]["end_frame"] == 1012 and d["color"]["manual_override"] is False


def test_inferred_requires_confirmation(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2, 3], attrs={"chromaticities": AP1})
    plan = plan_conversion(_req(src, tmp_path), ocio_cfg)
    assert not plan.ok and plan.exit_code is ExitCode.COLORSPACE
    plan = plan_conversion(_req(src, tmp_path, accept_inferred_colorspace=True), ocio_cfg)
    assert plan.ok and plan.input_colorspace_origin == "inferred_confirmed"


def test_unknown_requires_manual_and_override_is_recorded(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2])
    assert plan_conversion(_req(src, tmp_path), ocio_cfg).exit_code is ExitCode.COLORSPACE
    plan = plan_conversion(_req(src, tmp_path, input_colorspace="lin_rec709"), ocio_cfg)  # alias accepted
    assert plan.ok and plan.input_colorspace == "Linear Rec.709 (sRGB)" and plan.input_colorspace_origin == "manual"
    src2 = make_exr_sequence([1, 2], name="tagged", attrs={"colorInteropID": "lin_ap1_scene"}, directory=tmp_path / "t")
    plan = plan_conversion(_req(src2, tmp_path, input_colorspace="ACES2065-1"), ocio_cfg)
    assert plan.manual_override and plan.color_transforms == ["OCIO: 'ACES2065-1' -> 'ACEScg'"]


def test_invalid_colorspace_names_are_not_substituted(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(_req(src, tmp_path, input_colorspace="NotASpace", output_colorspace="AlsoNot"), ocio_cfg)
    assert len([e for e in plan.errors if e.code is ExitCode.COLORSPACE]) == 2
    assert plan.input_colorspace is None and plan.output_colorspace is None


def test_missing_frames_block_conversion(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1001, 1002, 1004], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(_req(src, tmp_path), ocio_cfg)
    assert plan.exit_code is ExitCode.INPUT and "1003" in plan.errors[0].message


def test_corrupt_and_mismatched_frames(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2, 3], attrs={"colorInteropID": "lin_ap1_scene"})
    (src / "plate.0002.exr").write_bytes(b"garbage")
    make_exr_sequence([3], size=(64, 36), directory=src, attrs={"colorInteropID": "lin_ap1_scene"})
    insp = inspect_source(src, ocio_cfg)
    assert any("corrupt" in e for e in insp.source.errors)
    assert any("resolution" in e for e in insp.source.errors)


def test_output_collisions(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "GOD_0046_005_ml_v001.1010.exr").write_bytes(b"")
    plan = plan_conversion(_req(src, out), ocio_cfg)
    assert plan.exit_code is ExitCode.OUTPUT and plan.collisions == ["GOD_0046_005_ml_v001.1010.exr"]
    plan = plan_conversion(_req(src, out, overwrite=True), ocio_cfg)
    assert plan.ok and any("overwritten" in w for w in plan.warnings)


def test_missing_output_dir_is_created_later(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(_req(src, tmp_path / "new" / "dir"), ocio_cfg)
    assert plan.ok and any("will be created" in w for w in plan.warnings)
    assert not (tmp_path / "new").exists()


def test_video_plan(ocio_cfg, make_video, tmp_path):
    src = make_video("clip.mp4", frames=10)
    plan = plan_conversion(_req(src, tmp_path, accept_inferred_colorspace=True), ocio_cfg)
    assert plan.ok, plan.errors
    assert plan.frame_count == 10 and plan.frame_range == (1009, 1018)
    assert plan.color_transforms[0].startswith("decode: YUV->RGB matrix bt709, tv range")


def test_bad_inputs(ocio_cfg, tmp_path):
    with pytest.raises(InputError):
        plan_conversion(_req(tmp_path / "missing.mov", tmp_path), ocio_cfg)
    (tmp_path / "clip.avi").write_bytes(b"")
    with pytest.raises(InputError):
        plan_conversion(_req(tmp_path / "clip.avi", tmp_path), ocio_cfg)
    with pytest.raises(ValidationError):
        plan_conversion(_req(tmp_path, tmp_path, shot="../../etc"), ocio_cfg)
