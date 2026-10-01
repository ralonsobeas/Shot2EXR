"""Resize geometry for FIT / FILL / STRETCH.

``compute_geometry`` is pure (used by dry runs); ``apply_geometry`` resamples pixels.
"""

from __future__ import annotations

from dataclasses import dataclass

from shot2exr.models import Resolution, ResizeMode


@dataclass(frozen=True)
class ResizeGeometry:
    """The source is scaled to ``scaled`` and placed at ``offset`` inside the target canvas.

    Negative offsets mean cropping (FILL); positive offsets mean padding (FIT).
    """

    source: Resolution
    target: Resolution
    mode: ResizeMode
    scaled: Resolution
    offset_x: int
    offset_y: int

    @property
    def is_identity(self) -> bool:
        return self.source == self.target and self.offset_x == 0 and self.offset_y == 0

    @property
    def pads(self) -> bool:
        return self.offset_x > 0 or self.offset_y > 0

    @property
    def crops(self) -> bool:
        return self.offset_x < 0 or self.offset_y < 0

    def describe(self) -> str:
        if self.mode is ResizeMode.STRETCH or (not self.pads and not self.crops):
            note = "aspect ratio changed" if self.mode is ResizeMode.STRETCH and not _same_aspect(self.source, self.target) else "no padding or cropping"
            return f"{self.source} -> {self.scaled} ({self.mode.value}, {note})"
        action = "padded" if self.pads else "cropped"
        return (
            f"{self.source} -> scaled {self.scaled}, {action} to {self.target} "
            f"(offset {self.offset_x},{self.offset_y})"
        )


def _same_aspect(a: Resolution, b: Resolution) -> bool:
    return a.width * b.height == b.width * a.height


def compute_geometry(source: Resolution, target: Resolution, mode: ResizeMode) -> ResizeGeometry:
    if mode is ResizeMode.STRETCH:
        return ResizeGeometry(source, target, mode, target, 0, 0)
    sx = target.width / source.width
    sy = target.height / source.height
    scale = min(sx, sy) if mode is ResizeMode.FIT else max(sx, sy)
    # Snap the axis that defines the scale exactly to the target to avoid off-by-one rounding.
    w = target.width if scale == sx else max(1, round(source.width * scale))
    h = target.height if scale == sy else max(1, round(source.height * scale))
    scaled = Resolution(w, h)
    return ResizeGeometry(source, target, mode, scaled, (target.width - w) // 2, (target.height - h) // 2)


def apply_geometry(pixels, geometry: ResizeGeometry):
    """Resample ``pixels`` (H, W, C float32, premultiplied alpha) to ``geometry.target``.

    Uses OIIO's Lanczos-3 filter (widened automatically when downscaling). Values are not
    clamped, so negative and over-range data survive (light ringing at hard edges is expected).
    Padding (FIT) is transparent black; FILL crops symmetrically.
    """
    import numpy as np
    import OpenImageIO as oiio

    src, scaled, target = geometry.source, geometry.scaled, geometry.target
    if (src.width, src.height) == (scaled.width, scaled.height):
        out = pixels
    else:
        nch = pixels.shape[2]
        buf = oiio.ImageBuf(oiio.ImageSpec(src.width, src.height, nch, oiio.FLOAT))
        buf.set_pixels(oiio.ROI(0, src.width, 0, src.height, 0, 1, 0, nch), np.ascontiguousarray(pixels))
        dst = oiio.ImageBuf(oiio.ImageSpec(scaled.width, scaled.height, nch, oiio.FLOAT))
        if not oiio.ImageBufAlgo.resize(dst, buf, filtername="lanczos3"):
            raise RuntimeError(f"Resize failed: {oiio.geterror()}")
        out = np.asarray(dst.get_pixels(oiio.FLOAT), dtype=np.float32).reshape(scaled.height, scaled.width, nch)
    if geometry.offset_x == 0 and geometry.offset_y == 0 and out.shape[:2] == (target.height, target.width):
        return out
    canvas = np.zeros((target.height, target.width, out.shape[2]), np.float32)
    # Intersection of the scaled image (placed at offset) with the canvas.
    ox, oy = geometry.offset_x, geometry.offset_y
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + scaled.width, target.width), min(oy + scaled.height, target.height)
    canvas[y0:y1, x0:x1] = out[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    return canvas
