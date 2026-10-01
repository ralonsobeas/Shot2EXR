"""EXR header inspection via OpenImageIO. Pixel reading is added in Milestone 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shot2exr.errors import DependencyError, InputError

# Header attributes that carry colour information (OpenEXR standard + ACES container + interop).
COLOR_ATTRIBUTES = ("colorInteropID", "chromaticities", "acesImageContainerFlag", "whiteLuminance", "adoptedNeutral")


def _oiio():
    try:
        import OpenImageIO as oiio
    except ImportError as exc:
        raise DependencyError(
            "OpenImageIO Python bindings are not available. Install 'py-openimageio' from conda-forge."
        ) from exc
    return oiio


def oiio_version() -> str | None:
    try:
        return _oiio().__version__
    except DependencyError:
        return None


@dataclass
class ExrHeader:
    path: Path
    width: int  # display window
    height: int
    data_window: tuple[int, int, int, int]  # x, y, width, height
    channels: list[str]
    pixel_type: str
    alpha_channel: int
    subimages: int
    compression: str | None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def has_alpha(self) -> bool:
        return self.alpha_channel >= 0

    @property
    def color_attributes(self) -> dict[str, Any]:
        return {k: self.attributes.get(k) for k in COLOR_ATTRIBUTES if k in self.attributes}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [_json_safe(v) for v in value]
    return str(value)


def read_header(path: Path) -> ExrHeader:
    """Read the first subimage header. Raises ``InputError`` for unreadable/corrupt files."""
    oiio = _oiio()
    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise InputError(f"Cannot read EXR header of {path.name}: {oiio.geterror() or 'unknown error'}")
    try:
        if inp.format_name() != "openexr":
            raise InputError(f"{path.name} is not an OpenEXR file (detected {inp.format_name()}).")
        spec = inp.spec()
        attrs = {a.name: _json_safe(a.value) for a in spec.extra_attribs}
        return ExrHeader(
            path=path,
            width=spec.full_width,
            height=spec.full_height,
            data_window=(spec.x, spec.y, spec.width, spec.height),
            channels=list(spec.channelnames),
            pixel_type=str(spec.format),
            alpha_channel=spec.alpha_channel,
            subimages=int(attrs.get("oiio:subimages", 1) or 1),
            compression=attrs.get("compression"),
            attributes=attrs,
        )
    finally:
        inp.close()


@dataclass
class Frame:
    """Float32 pixels of the display window, shape (height, width, channels); RGB or RGBA."""

    pixels: Any
    channels: list[str]

    @property
    def has_alpha(self) -> bool:
        return self.channels[-1] == "A"


def read_frame(path: Path, want_alpha: bool = True) -> Frame:
    """Read one EXR frame as float32 RGB(A), resolved to the display window.

    Pixels of the data window outside the display window are discarded; display-window areas
    not covered by the data window are zero (transparent black), as OpenEXR defines.
    """
    import numpy as np

    oiio = _oiio()
    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise InputError(f"Cannot open {path.name}: {oiio.geterror() or 'unknown error'}")
    try:
        spec = inp.spec()
        names = list(spec.channelnames)
        missing = [c for c in ("R", "G", "B") if c not in names]
        if missing:
            raise InputError(f"{path.name} has no {'/'.join(missing)} channel(s) (channels: {', '.join(names)}).")
        wanted = ["R", "G", "B"] + (["A"] if want_alpha and "A" in names else [])
        data = inp.read_image(0, 0, 0, spec.nchannels, "float")
        if data is None:
            raise InputError(f"Cannot read pixels of {path.name}: {inp.geterror() or 'unknown error'}")
    finally:
        inp.close()
    data = np.asarray(data, dtype=np.float32).reshape(spec.height, spec.width, spec.nchannels)
    data = data[:, :, [names.index(c) for c in wanted]]

    full_w, full_h = spec.full_width, spec.full_height
    if (spec.x, spec.y, spec.width, spec.height) == (spec.full_x, spec.full_y, full_w, full_h):
        return Frame(np.ascontiguousarray(data), wanted)
    canvas = np.zeros((full_h, full_w, len(wanted)), np.float32)
    # Data window origin relative to the display window origin.
    ox, oy = spec.x - spec.full_x, spec.y - spec.full_y
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + spec.width, full_w), min(oy + spec.height, full_h)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = data[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    return Frame(canvas, wanted)
