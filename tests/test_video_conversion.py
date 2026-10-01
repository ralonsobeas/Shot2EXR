"""Video -> EXR integration tests (Milestone 3). Media is generated with FFmpeg on the fly."""

import json
import subprocess
import threading
from pathlib import Path

import pytest

from shot2exr.converter import plan_conversion, run_conversion
from shot2exr.exr_reader import read_frame, read_header
from shot2exr.models import ConversionRequest, Resolution
from shot2exr.video_reader import build_command

np = pytest.importorskip("numpy")
pytest.importorskip("OpenImageIO")
BASE = "PROJ_0010_020_comp_v001"
G24 = "Gamma 2.4 Encoded Rec.709"
TAGS_709 = "color_primaries=bt709:color_trc=bt709:colorspace=bt709"


@pytest.fixture
def ffmpeg(tmp_path, ffmpeg_bin):
    def _run(name, lavfi, *out_args, frames=10, vf=None):
        path = tmp_path / name
        cmd = [ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, "-frames:v", str(frames)]
        if vf:
            cmd += ["-vf", vf]
        subprocess.run(cmd + list(out_args) + [str(path)], check=True)
        return path
    return _run


def convert(cfg, src, out, **kw):
    params = dict(input_path=Path(src), project="PROJ", shot="0010_020", task="comp", version="001", start_frame=1009,
                  output_resolution=Resolution(32, 18), element="water", output_directory=Path(out),
                  accept_inferred_colorspace=True)
    params.update(kw)
    plan = plan_conversion(ConversionRequest(**params), cfg)
    assert plan.ok, plan.errors
    return plan


def mean_rgb(path):
    return read_frame(path).pixels[..., :3].mean(axis=(0, 1))


def test_every_frame_once_in_presentation_order(ocio_cfg, ffmpeg, tmp_path):
    # Luma steps up by 8 per frame; lossless H.264 with B-frames (decode order != presentation order).
    src = ffmpeg("ramp.mp4", "color=black:size=32x18:rate=24,format=yuv444p", "-c:v", "libx264", "-qp", "0",
                 "-bf", "3", "-pix_fmt", "yuv444p", frames=20, vf=f"geq=lum='16+N*8':cb=128:cr=128,setparams={TAGS_709}:range=tv")
    out = tmp_path / "out"
    plan = convert(ocio_cfg, src, out, output_colorspace=G24)
    res = run_conversion(plan, ocio_cfg)
    assert res.ok and res.frames_written == 20
    files = sorted(out.glob("*.exr"))
    assert [f.name for f in files] == [f"{BASE}.{n}.exr" for n in range(1009, 1029)]
    means = [mean_rgb(f)[0] for f in files]
    assert all(b - a > 0.02 for a, b in zip(means, means[1:]))  # strictly increasing: no dup/drop/reorder
    np.testing.assert_allclose(means[0], 0.0, atol=2e-3)  # Y=16 limited -> 0.0
    report = json.loads(res.report_path.read_text())
    assert report["input"]["frames_decoded"] == 20 and report["validation"]["result"] == "passed"
    assert report["color_management"]["transforms_performed"][0].startswith("decode (FFmpeg")
    assert report["color_management"]["decode"]["matrix"] == "bt709"


@pytest.mark.parametrize("rng", ["tv", "pc"])
def test_range_and_matrix_decoding(ocio_cfg, ffmpeg, tmp_path, rng):
    vf = f"scale=out_range={rng}:out_color_matrix=bt709,setparams={TAGS_709}:range={rng}"
    src = ffmpeg(f"grey_{rng}.mov", "color=c=0x808080:size=32x18:rate=24", "-c:v", "libx264", "-qp", "0",
                 "-pix_fmt", "yuv444p", "-color_range", rng, frames=2, vf=vf)
    white = ffmpeg(f"white_{rng}.mov", "color=c=white:size=32x18:rate=24", "-c:v", "libx264", "-qp", "0",
                   "-pix_fmt", "yuv444p", "-color_range", rng, frames=2, vf=vf)
    for clip, expected in ((src, 128 / 255), (white, 1.0)):
        out = tmp_path / f"out_{clip.stem}"
        run_conversion(convert(ocio_cfg, clip, out, output_colorspace=G24), ocio_cfg)
        np.testing.assert_allclose(mean_rgb(out / f"{BASE}.1009.exr"), expected, atol=6e-3)


def test_colour_conversion_to_acescg(ocio_cfg, ffmpeg, tmp_path):
    src = ffmpeg("grey.mp4", "color=c=0x808080:size=32x18:rate=24", "-c:v", "libx264", "-qp", "0",
                 "-pix_fmt", "yuv444p", frames=2, vf=f"setparams={TAGS_709}:range=tv")
    out = tmp_path / "out"
    plan = convert(ocio_cfg, src, out)  # output ACEScg (default)
    run_conversion(plan, ocio_cfg)
    decoded = read_frame(out / f"{BASE}.1009.exr").pixels
    expected = np.full((1, 1, 3), 128 / 255, np.float32)
    ocio_cfg.processor(G24, "ACEScg").applyRGB(expected)
    np.testing.assert_allclose(decoded.mean(axis=(0, 1)), expected[0, 0], rtol=0.03)
    assert read_header(out / f"{BASE}.1009.exr").attributes["colorInteropID"] == "lin_ap1_scene"


def test_prores_alpha_is_premultiplied(ocio_cfg, ffmpeg, tmp_path):
    src = ffmpeg("alpha.mov", "color=c=red:size=32x18:rate=24", "-c:v", "prores_ks", "-profile:v", "4444",
                 "-pix_fmt", "yuva444p10le", frames=2,
                 vf=f"format=rgba,colorchannelmixer=aa=0.5,setparams={TAGS_709}:range=tv")
    out = tmp_path / "out"
    plan = convert(ocio_cfg, src, out, output_colorspace=G24)
    assert plan.inspection.source.has_alpha
    run_conversion(plan, ocio_cfg)
    hdr = read_header(out / f"{BASE}.1009.exr")
    px = read_frame(out / f"{BASE}.1009.exr").pixels.mean(axis=(0, 1))
    assert hdr.channels == ["R", "G", "B", "A"]
    np.testing.assert_allclose(px, [0.5, 0.0, 0.0, 0.5], atol=0.02)  # straight red @50% -> premultiplied


def test_variable_frame_rate_keeps_every_frame(ocio_cfg, ffmpeg, tmp_path):
    src = ffmpeg("vfr.mp4", "testsrc2=size=32x18:rate=24", "-fps_mode", "passthrough", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", frames=10, vf=f"setpts='if(lt(N,5),N,10+N*2)/24/TB',setparams={TAGS_709}:range=tv")
    out = tmp_path / "out"
    plan = convert(ocio_cfg, src, out)
    assert plan.inspection.source.variable_frame_rate is True
    res = run_conversion(plan, ocio_cfg)
    assert res.ok and len(list(out.glob("*.exr"))) == 10
    timing = json.loads(res.report_path.read_text())["input"]["timing"]
    assert timing["constant"] is False and timing["max_frame_duration_s"] > timing["min_frame_duration_s"]


def test_frame_count_mismatch_fails_safely(ocio_cfg, make_video, tmp_path):
    src = make_video("ten.mov", frames=10)
    for wrong, needle in ((12, "produced 10 frames but 12"), (8, "more frames than the 8")):
        out = tmp_path / f"out{wrong}"
        plan = convert(ocio_cfg, src, out)
        plan.frame_count, plan.frame_range = wrong, (1009, 1009 + wrong - 1)
        res = run_conversion(plan, ocio_cfg)
        assert res.status == "failed" and needle in res.errors[0]
        assert [p.name for p in out.iterdir()] == [f"{BASE}.conversion_report.FAILED.json"]


def test_video_cancel_stops_ffmpeg_and_keeps_nothing(ocio_cfg, make_video, tmp_path):
    out = tmp_path / "out"
    plan = convert(ocio_cfg, make_video("c.mov", frames=20), out)
    cancel = threading.Event()
    res = run_conversion(plan, ocio_cfg, cancel=cancel, progress=lambda *a: cancel.set())
    assert res.status == "cancelled"
    assert [p.name for p in out.iterdir()] == [f"{BASE}.conversion_report.CANCELLED.json"]


def test_decoder_command_is_explicit():
    cmd = build_command(Path("ffmpeg"), Path("/a b/c.mov"), {"yuv_to_rgb": True, "matrix": "smpte170m", "range": "pc"}, False)
    assert str(Path("/a b/c.mov")) in cmd and "-noautorotate" in cmd and cmd[cmd.index("-fps_mode") + 1] == "passthrough"
    assert "in_color_matrix=bt601" in cmd[cmd.index("-vf") + 1] and "in_range=pc" in cmd[cmd.index("-vf") + 1]
    rgb = build_command(Path("ffmpeg"), Path("x.mov"), {"yuv_to_rgb": False, "range": "pc"}, True)
    assert "in_color_matrix" not in rgb[rgb.index("-vf") + 1] and rgb[rgb.index("-pix_fmt") + 1] == "rgba64le"


def test_cli_video_conversion(make_video, tmp_path):
    from shot2exr.cli import main

    out = tmp_path / "out"
    args = ["--input", str(make_video("cli clip.mp4", frames=4)), "--project", "PROJ", "--shot", "0010_020",
            "--task", "comp", "--element", "water", "--version", "1", "--start-frame", "1009", "--resolution", "64x36",
            "--output-dir", str(out), "--accept-inferred-colorspace"]
    assert main(args) == 0
    assert len(list(out.glob("*.exr"))) == 4 and (out / f"{BASE}.conversion_report.json").is_file()
