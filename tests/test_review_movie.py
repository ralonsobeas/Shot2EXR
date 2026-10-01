"""The ProRes review movie written next to every EXR sequence."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from shot2exr import review_movie
from shot2exr.converter import plan_conversion, review_rgb, run_conversion
from shot2exr.errors import ExitCode
from shot2exr.media_probe import probe_video
from shot2exr.models import ConversionRequest, Resolution

BASE = "PROJ_0010_020_comp_v001"
pytestmark = pytest.mark.usefixtures("ffmpeg_bin")


def request(src, out, **kw):
    params = dict(input_path=Path(src), project="PROJ", shot="0010_020", task="comp", version="001", start_frame=1009,
                  output_resolution=Resolution(32, 18), element="water", output_directory=Path(out))
    params.update(kw)
    return ConversionRequest(**params)


def convert(cfg, src, out, **kw):
    plan = plan_conversion(request(src, out, **kw), cfg)
    assert plan.ok, plan.errors
    return plan, run_conversion(plan, cfg)


def stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,color_primaries,color_transfer,color_space",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def test_exr_input_writes_prores_movie_at_default_fps(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2, 3], attrs={"colorInteropID": "lin_ap1_scene"})
    plan, res = convert(ocio_cfg, src, tmp_path / "out")
    assert res.ok
    mov = tmp_path / "out" / f"{BASE}.mov"
    info = stream(mov)
    assert (info["codec_name"], info["pix_fmt"], info["width"], info["height"]) == ("prores", "yuv422p10le", 32, 18)
    assert info["r_frame_rate"] == "24/1" and info["color_primaries"] == "bt709" and info["color_transfer"] == "bt709"
    assert probe_video(mov).frame_count == 3
    report = json.loads(res.report_path.read_text())
    movie = report["output"]["review_movie"]
    assert movie["file"] == mov.name and movie["frames"] == 3 and movie["fps"] == "24" and movie["fps_origin"] == "default"
    assert movie["display"] == "Rec.1886 Rec.709 - Display" and "never" in movie["note"]
    assert mov.name in report["output"]["files"]
    assert any("24 fps" in w for w in plan.warnings)


def test_video_input_keeps_source_fps(ocio_cfg, make_video, tmp_path):
    clip = make_video(frames=5)
    plan, res = convert(ocio_cfg, clip, tmp_path / "out", output_resolution=Resolution(160, 90),
                        accept_inferred_colorspace=True)
    assert res.ok
    info = stream(tmp_path / "out" / f"{BASE}.mov")
    assert info["r_frame_rate"] == "24/1"
    assert plan.review_movie()["fps_origin"] == "source"


def test_odd_resolution_is_padded_to_even(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    _, res = convert(ocio_cfg, src, tmp_path / "out", output_resolution=Resolution(33, 19))
    assert res.ok
    info = stream(tmp_path / "out" / f"{BASE}.mov")
    assert (info["width"], info["height"]) == (34, 20)


def test_movie_pixels_use_display_transform_and_exrs_are_untouched(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})  # flat 0.18 ACEScg
    _, res = convert(ocio_cfg, src, tmp_path / "out")
    from shot2exr.exr_reader import read_frame

    exr = read_frame(tmp_path / "out" / f"{BASE}.1009.exr").pixels
    np.testing.assert_allclose(exr, 0.18, atol=1e-3)  # scene-linear, unchanged by the review transform
    expected = review_rgb(ocio_cfg.display_processor("ACEScg"), exr)[9, 16]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "out" / f"{BASE}.mov"), "-f", "rawvideo",
                          "-pix_fmt", "rgb48le", "-"], capture_output=True, check=True).stdout
    got = np.frombuffer(raw, "<u2").reshape(18, 32, 3)[9, 16] / 65535.0
    assert 0.3 < expected[1] < 0.6  # mid grey on a Rec.709 display, not linear 0.18
    np.testing.assert_allclose(got, expected, atol=0.01)


def test_review_rgb_copies_and_skips_alpha(ocio_cfg):
    px = np.full((2, 2, 4), 0.18, np.float32)
    rgb = review_rgb(ocio_cfg.display_processor("ACEScg"), px)
    assert rgb.shape == (2, 2, 3) and np.all(px == np.float32(0.18)) and rgb[0, 0, 0] > 0.3


def test_data_space_has_no_display_transform(ocio_cfg):
    data = next(e.name for e in ocio_cfg.colorspaces() if e.is_data)
    assert ocio_cfg.display_processor(data) is None


def test_dry_run_names_movie_and_writes_nothing(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(request(src, tmp_path / "out"), ocio_cfg)
    assert plan.ok and plan.to_dict()["output"]["review_movie"]["file"] == f"{BASE}.mov"
    assert not (tmp_path / "out").exists()


def test_existing_movie_blocks_without_overwrite(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1, 2], attrs={"colorInteropID": "lin_ap1_scene"})
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{BASE}.mov").write_bytes(b"old")
    plan = plan_conversion(request(src, out), ocio_cfg)
    assert plan.exit_code is ExitCode.OUTPUT and plan.collisions == [f"{BASE}.mov"]
    plan = plan_conversion(request(src, out, overwrite=True), ocio_cfg)
    assert plan.ok and run_conversion(plan, ocio_cfg).ok
    assert (out / f"{BASE}.mov").stat().st_size > 3


def test_encoder_failure_fails_the_whole_conversion(ocio_cfg, make_exr_sequence, tmp_path, monkeypatch):
    real = review_movie.build_command

    def broken(*a, **k):
        cmd = real(*a, **k)
        cmd[cmd.index("prores_ks")] = "no_such_encoder"
        return cmd

    monkeypatch.setattr(review_movie, "build_command", broken)
    src = make_exr_sequence([1, 2, 3], attrs={"colorInteropID": "lin_ap1_scene"})
    out = tmp_path / "out"
    plan = plan_conversion(request(src, out), ocio_cfg)
    res = run_conversion(plan, ocio_cfg)
    assert res.status == "failed" and res.exit_code is ExitCode.OUTPUT and "review movie" in res.errors[0]
    assert sorted(p.name for p in out.iterdir()) == [f"{BASE}.conversion_report.FAILED.json"]
    assert not any(p.name.startswith(".") for p in tmp_path.iterdir())


def test_missing_ffmpeg_is_a_plan_error_for_exr_input(ocio_cfg, make_exr_sequence, tmp_path):
    src = make_exr_sequence([1], attrs={"colorInteropID": "lin_ap1_scene"})
    plan = plan_conversion(request(src, tmp_path / "out", ffmpeg_path=str(tmp_path / "nope")), ocio_cfg)
    assert plan.exit_code is ExitCode.DEPENDENCY


def test_movie_fps():
    assert review_movie.movie_fps("24000/1001") == ("24000/1001", "source")
    assert review_movie.movie_fps(None) == ("24", "default")
    assert review_movie.movie_fps("0/0") == ("24", "default")


def test_threaded_review_transform_matches_single_thread(ocio_cfg):
    from concurrent.futures import ThreadPoolExecutor

    rng = np.random.default_rng(0)
    px = (rng.random((37, 50, 4), np.float32) * 4.0).astype(np.float32)
    proc = ocio_cfg.display_processor("ACEScg")
    with ThreadPoolExecutor(3) as pool:
        np.testing.assert_array_equal(review_rgb(proc, px, pool), review_rgb(proc, px))
