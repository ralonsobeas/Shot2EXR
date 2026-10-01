"""EXR -> EXR integration tests (Milestone 2): pixels, colour, resize, alpha, safety, report."""

import json
import threading
from pathlib import Path

import pytest

from shot2exr import converter
from shot2exr.converter import plan_conversion, run_conversion
from shot2exr.errors import ExitCode, Shot2EXRError
from shot2exr.exr_reader import read_frame, read_header
from shot2exr.models import ConversionRequest, Resolution, ResizeMode

oiio = pytest.importorskip("OpenImageIO")
np = pytest.importorskip("numpy")
BASE = "PROJ_0010_020_comp_v001"


def write_exr(path, pixels, attrs=None, data_window=None):
    h, w, c = pixels.shape
    spec = oiio.ImageSpec(w, h, c, oiio.FLOAT)
    if c == 4:
        spec.channelnames = ("R", "G", "B", "A")
        spec.alpha_channel = 3
    if data_window:  # (x, y, full_w, full_h)
        spec.x, spec.y = data_window[0], data_window[1]
        spec.full_width, spec.full_height = data_window[2], data_window[3]
    for k, v in (attrs or {}).items():
        spec.attribute(k, v)
    out = oiio.ImageOutput.create("exr")
    assert out.open(str(path), spec)
    out.write_image(pixels.astype(np.float32))
    out.close()


def gradient(w=48, h=27, alpha=False):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    px = np.stack([x / w * 4.0 - 0.5, y / h * 20.0, np.full_like(x, -0.25)], axis=-1)  # negatives + over-range
    if alpha:
        px = np.concatenate([px * 0.5, np.full((h, w, 1), 0.5, np.float32)], axis=-1)  # premultiplied
    return px


@pytest.fixture
def source(tmp_path):
    def _make(n=3, alpha=False, attrs=None, size=(48, 27)):
        d = tmp_path / "src"
        d.mkdir(exist_ok=True)
        for i in range(n):
            write_exr(d / f"plate.{1001 + i}.exr", gradient(*size, alpha=alpha) + i * 0.01,
                      attrs or {"colorInteropID": "lin_ap1_scene"})
        return d
    return _make


def convert(cfg, src, out, **kw):
    params = dict(input_path=Path(src), project="PROJ", shot="0010_020", task="comp", version="001", start_frame=1009,
                  output_resolution=Resolution(48, 27), element="water", output_directory=Path(out))
    params.update(kw)
    plan = plan_conversion(ConversionRequest(**params), cfg)
    assert plan.ok, plan.errors
    return plan, run_conversion(plan, cfg)


def pixels(path):
    return read_frame(path).pixels


def test_identity_preserves_values_names_and_report(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    plan, res = convert(ocio_cfg, source(3), out)
    assert res.ok and res.frames_written == 3
    names = sorted(p.name for p in out.iterdir())
    assert names == [f"{BASE}.1009.exr", f"{BASE}.1010.exr", f"{BASE}.1011.exr", f"{BASE}.conversion_report.json",
                     f"{BASE}.mov"]
    src_px = pixels(tmp_path / "src" / "plate.1001.exr")
    out_px = pixels(out / f"{BASE}.1009.exr")
    np.testing.assert_allclose(out_px, src_px, rtol=1e-3, atol=1e-3)  # half precision
    assert out_px.min() < 0 and out_px.max() > 10  # negatives and over-range kept
    hdr = read_header(out / f"{BASE}.1009.exr")
    assert hdr.pixel_type == "half" and hdr.compression == "zip" and hdr.channels == ["R", "G", "B"]
    assert hdr.attributes["colorInteropID"] == "lin_ap1_scene" and len(hdr.attributes["chromaticities"]) == 8
    report = json.loads((out / f"{BASE}.conversion_report.json").read_text())
    assert report["general"]["status"] == "success" and report["validation"]["result"] == "passed"
    assert report["output"]["element"] == "water" and report["output"]["output_directory"] == str(out)
    assert report["output"]["start_frame"] == 1009 and report["output"]["end_frame"] == 1011
    assert report["input"]["original_frame_range"] == [1001, 1003]
    assert report["color_management"]["transforms_performed"] == ["none (input and output are both 'ACEScg', no resize)"]
    assert report["input"]["fps"] is None  # unknown stays null


def test_ocio_transform_matches_processor(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    _, res = convert(ocio_cfg, source(1), out, output_colorspace="ACES2065-1")
    expected = pixels(tmp_path / "src" / "plate.1001.exr").copy()
    ocio_cfg.processor("ACEScg", "ACES2065-1").applyRGB(expected)
    np.testing.assert_allclose(pixels(out / f"{BASE}.1009.exr"), expected, rtol=2e-3, atol=2e-3)
    assert read_header(out / f"{BASE}.1009.exr").attributes["colorInteropID"] == "lin_ap0_scene"


def test_alpha_unpremultiplied_around_nonlinear_transform(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    _, res = convert(ocio_cfg, source(1, alpha=True), out, output_colorspace="sRGB Encoded Rec.709 (sRGB)")
    src = pixels(tmp_path / "src" / "plate.1001.exr")
    rgb = np.ascontiguousarray(src[..., :3] / 0.5)
    ocio_cfg.processor("ACEScg", "sRGB Encoded Rec.709 (sRGB)").applyRGB(rgb)
    got = pixels(out / f"{BASE}.1009.exr")
    assert read_header(out / f"{BASE}.1009.exr").channels == ["R", "G", "B", "A"]
    np.testing.assert_allclose(got[..., 3], 0.5, atol=1e-3)
    np.testing.assert_allclose(got[..., :3], rgb * 0.5, rtol=3e-3, atol=3e-3)


@pytest.mark.parametrize("mode", list(ResizeMode))
def test_resize_modes_exact_dimensions(ocio_cfg, source, tmp_path, mode):
    out = tmp_path / mode.value
    _, res = convert(ocio_cfg, source(1, size=(64, 16)), out, output_resolution=Resolution(40, 30), resize_mode=mode)
    px = pixels(out / f"{BASE}.1009.exr")
    assert px.shape == (30, 40, 3)
    if mode is ResizeMode.FIT:  # 64x16 -> 40x10, letterboxed with black
        assert np.all(px[:10] == 0) and np.all(px[-10:] == 0) and np.any(px[10:20] != 0)
    else:
        assert np.any(px[0] != 0) and np.any(px[-1] != 0)


def test_resize_happens_in_scene_linear_for_nonlinear_input(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    src = source(1, attrs={"colorInteropID": "srgb_rec709_scene"})
    plan, res = convert(ocio_cfg, src, out, output_resolution=Resolution(24, 14), output_colorspace="ACEScg")
    steps = json.loads(res.report_path.read_text())["color_management"]["transforms_performed"]
    assert steps[0] == "OCIO 'sRGB Encoded Rec.709 (sRGB)' -> 'ACEScg' (scene-linear working space for resizing)"
    assert steps[1].startswith("resize (lanczos3, in 'ACEScg')") and len(steps) == 2


def test_data_window_resolved_to_display_window(tmp_path):
    path = tmp_path / "overscan.0001.exr"
    write_exr(path, np.ones((4, 4, 3), np.float32), data_window=(2, 1, 8, 6))
    px = read_frame(path).pixels
    assert px.shape == (6, 8, 3) and px[1:5, 2:6].min() == 1 and px.sum() == 48


def test_overwrite_protection_and_overwrite(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    src = source(2)
    convert(ocio_cfg, src, out)
    req = dict(input_path=src, project="PROJ", shot="0010_020", task="comp", version="001", start_frame=1009,
               output_resolution=Resolution(48, 27), element="water", output_directory=out)
    plan = plan_conversion(ConversionRequest(**req), ocio_cfg)
    assert plan.exit_code is ExitCode.OUTPUT and len(plan.collisions) == 4
    with pytest.raises(Shot2EXRError):
        run_conversion(plan, ocio_cfg)
    _, res = convert(ocio_cfg, src, out, overwrite=True, output_colorspace="ACES2065-1")
    assert res.ok and read_header(out / f"{BASE}.1009.exr").attributes["colorInteropID"] == "lin_ap0_scene"


def test_cancel_leaves_no_partial_sequence(ocio_cfg, source, tmp_path):
    out = tmp_path / "out"
    src = source(4)
    cancel = threading.Event()
    req = ConversionRequest(input_path=src, project="PROJ", shot="0010_020", task="comp", version="001", start_frame=1009,
                            output_resolution=Resolution(48, 27), element="water", output_directory=out)
    plan = plan_conversion(req, ocio_cfg)
    res = run_conversion(plan, ocio_cfg, cancel=cancel, progress=lambda done, total, name: cancel.set())
    assert res.status == "cancelled" and res.exit_code is ExitCode.CANCELLED
    assert sorted(p.name for p in out.iterdir()) == [f"{BASE}.conversion_report.CANCELLED.json"]
    report = json.loads(res.report_path.read_text())
    assert report["output"]["partial_frames_removed"] == 1 and report["validation"]["result"] == "failed"
    # A retry is allowed and replaces the stale report.
    plan = plan_conversion(req, ocio_cfg)
    assert plan.ok and run_conversion(plan, ocio_cfg).ok
    assert not (out / f"{BASE}.conversion_report.CANCELLED.json").exists()


def test_failure_mid_sequence_writes_failed_report(ocio_cfg, source, tmp_path, monkeypatch):
    root = tmp_path / "Projects"
    root.mkdir()
    real_write = converter.write_frame
    calls = []

    def flaky(path, *a, **k):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real_write(path, *a, **k)

    monkeypatch.setattr(converter, "write_frame", flaky)
    from shot2exr.settings import load_settings

    req = ConversionRequest(input_path=source(3), project="PROJ", shot="0010_020", task="comp", version="001",
                            start_frame=1009, output_resolution=Resolution(48, 27), element="water",
                            projects_root=str(root))
    plan = plan_conversion(req, ocio_cfg, settings=load_settings())
    res = run_conversion(plan, ocio_cfg)
    vdir = Path(plan.location.directory)
    assert res.status == "failed" and "disk full" in res.errors[0]
    assert sorted(p.name for p in vdir.iterdir()) == [f"{BASE}.conversion_report.FAILED.json"]
    assert not any(p.name.startswith(".") for p in vdir.parent.iterdir())  # staging removed


def test_auto_directory_created_only_on_success(ocio_cfg, source, tmp_path):
    from shot2exr.settings import load_settings

    root = tmp_path / "Projects"
    root.mkdir()
    req = ConversionRequest(input_path=source(2), project="PROJ", shot="0010_020", task="comp", version="003",
                            start_frame=1009, output_resolution=Resolution(48, 27), element="smoke",
                            projects_root=str(root))
    plan = plan_conversion(req, ocio_cfg, settings=load_settings())
    vdir = root / "MyProject/VFX/PROJ_0010/PROJ_0010_020/Tasks/Compositing/ComfyUI/smoke/PROJ_0010_020_comp_v003"
    assert not vdir.exists()
    res = run_conversion(plan, ocio_cfg)
    assert res.ok and res.output_directory == vdir
    assert "PROJ_0010_020_comp_v003.conversion_report.json" in {p.name for p in vdir.iterdir()}
    assert json.loads(res.report_path.read_text())["output"]["projects_root"] == str(root)
