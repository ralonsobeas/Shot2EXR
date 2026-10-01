"""EXR output via OpenImageIO: half float, ZIP, RGB(A), fresh header (input metadata is never copied)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shot2exr import TOOL_NAME, __version__, config
from shot2exr.errors import OutputError
from shot2exr.exr_reader import _oiio


def write_frame(path: Path, pixels: Any, channels: list[str], attributes: dict[str, Any] | None = None) -> None:
    """Write ``pixels`` (H, W, C float32) to ``path``. ``attributes`` may include ``chromaticities`` (8 floats)."""
    oiio = _oiio()
    height, width, nch = pixels.shape
    spec = oiio.ImageSpec(width, height, nch, oiio.HALF)
    spec.channelnames = tuple(channels)
    spec.alpha_channel = channels.index("A") if "A" in channels else -1
    spec.attribute("compression", config.EXR_COMPRESSION)
    spec.attribute("Software", f"{TOOL_NAME} {__version__}")
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        if key == "chromaticities":
            spec.attribute(key, oiio.TypeDesc("float[8]"), tuple(float(v) for v in value))
        else:
            spec.attribute(key, value)
    out = oiio.ImageOutput.create(str(path))
    if out is None or not out.open(str(path), spec):
        raise OutputError(f"Cannot create {path}: {oiio.geterror() or (out.geterror() if out else 'unknown error')}")
    try:
        if not out.write_image(pixels):
            raise OutputError(f"Cannot write {path.name}: {out.geterror()}")
    finally:
        out.close()
