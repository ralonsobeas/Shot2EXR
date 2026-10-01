"""Resize geometry for FIT / FILL / STRETCH.

Milestone 1 only computes geometry (shown in dry runs); pixel resampling is added in Milestone 2.
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
