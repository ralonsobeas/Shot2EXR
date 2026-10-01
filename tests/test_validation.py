from pathlib import Path

import pytest

from shot2exr.errors import ValidationError
from shot2exr.models import ConversionRequest, Resolution, ResizeMode
from shot2exr.resize import compute_geometry
from shot2exr.validation import parse_resize_mode, parse_resolution, validate_request


def _req(**kw):
    base = dict(input_path=Path("in.mov"), project="GOD", shot="0046_005", task="ml", version="1",
                start_frame=1009, output_resolution=Resolution(2048, 1152), element="water", output_directory=Path("out"))
    base.update(kw)
    return ConversionRequest(**base)


@pytest.mark.parametrize("text,res", [("2048x1152", (2048, 1152)), (" 1920 X 1080 ", (1920, 1080))])
def test_parse_resolution(text, res):
    assert parse_resolution(text) == Resolution(*res)


@pytest.mark.parametrize("text", ["2048", "0x100", "2048x-1", "axb", "", "40000x10"])
def test_parse_resolution_rejects(text):
    with pytest.raises(ValidationError):
        parse_resolution(text)


def test_resize_mode():
    assert parse_resize_mode("FILL") is ResizeMode.FILL
    with pytest.raises(ValidationError):
        parse_resize_mode("crop")


def test_validate_request_normalizes():
    req = validate_request(_req(version="v7", resize_mode="stretch"))
    assert req.version == "v007" and req.resize_mode is ResizeMode.STRETCH


def test_validate_request_collects_all_problems():
    with pytest.raises(ValidationError) as exc:
        validate_request(_req(project="../x", task="", version="abc", output_colorspace="auto", element="a/b"))
    assert len(exc.value.problems) == 5


def test_manual_output_directory_is_optional():
    assert validate_request(_req(output_directory=None)).output_directory is None
    assert validate_request(_req(output_directory="")).output_directory is None


@pytest.mark.parametrize("mode,scaled,offset", [
    (ResizeMode.FIT, (2048, 858), (0, 147)),     # 2.39 source into 16:9 -> letterbox
    (ResizeMode.FILL, (2750, 1152), (-351, 0)),  # crop sides (2749.76 rounds up)
    (ResizeMode.STRETCH, (2048, 1152), (0, 0)),
])
def test_resize_geometry(mode, scaled, offset):
    g = compute_geometry(Resolution(4096, 1716), Resolution(2048, 1152), mode)
    assert (g.scaled.width, g.scaled.height) == scaled
    assert (g.offset_x, g.offset_y) == offset
