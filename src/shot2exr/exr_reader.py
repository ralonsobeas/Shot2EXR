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
