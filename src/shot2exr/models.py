"""Plain data models passed between the engine, CLI and GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from shot2exr import config


class SourceType(str, Enum):
    VIDEO = "video"
    EXR_SEQUENCE = "exr_sequence"


class ResizeMode(str, Enum):
    FIT = "fit"
    FILL = "fill"
    STRETCH = "stretch"


class DetectionState(str, Enum):
    DETECTED = "DETECTED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Resolution:
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class ExrSequence:
    """A numbered EXR sequence found on disk. ``frames`` is sorted and maps 1:1 to ``files``."""

    directory: Path
    prefix: str
    extension: str
    padding: int
    frames: list[int]
    files: list[Path]
    issues: list[str] = field(default_factory=list)

    @property
    def first(self) -> int:
        return self.frames[0]

    @property
    def last(self) -> int:
        return self.frames[-1]

    @property
    def count(self) -> int:
        return len(self.frames)

    @property
    def missing_frames(self) -> list[int]:
        present = set(self.frames)
        return [f for f in range(self.first, self.last + 1) if f not in present]

    @property
    def pattern(self) -> str:
        return f"{self.prefix}{'#' * self.padding}{self.extension}"


@dataclass
class ColorDetection:
    state: DetectionState
    colorspace: str | None  # name in the active OCIO config
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # Video only: how YUV is turned into RGB *before* OCIO (matrix, range).
    decode: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "colorspace": self.colorspace,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "decode": self.decode,
        }


@dataclass
class SourceInfo:
    path: Path
    source_type: SourceType
    resolution: Resolution | None = None
    frame_count: int | None = None
    frame_count_method: str | None = None  # e.g. "nb_frames", "count_packets", "files", "estimated"
    frame_range: tuple[int, int] | None = None
    missing_frames: list[int] = field(default_factory=list)
    fps: str | None = None  # rational string, e.g. "24000/1001"
    time_base: str | None = None
    variable_frame_rate: bool | None = None
    codec: str | None = None
    pixel_format: str | None = None
    channels: list[str] = field(default_factory=list)
    has_alpha: bool | None = None
    color_metadata: dict[str, Any] = field(default_factory=dict)
    sequence: ExrSequence | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Problems that make the source unconvertible (missing/corrupt frames, ...).
    errors: list[str] = field(default_factory=list)

    @property
    def frame_count_exact(self) -> bool:
        return self.frame_count is not None and self.frame_count_method != "estimated"

    def to_dict(self) -> dict[str, Any]:
        seq = self.sequence
        return {
            "path": str(self.path),
            "source_type": self.source_type.value,
            "resolution": str(self.resolution) if self.resolution else None,
            "frame_count": self.frame_count,
            "frame_count_method": self.frame_count_method,
            "frame_range": list(self.frame_range) if self.frame_range else None,
            "missing_frames": self.missing_frames,
            "fps": self.fps,
            "time_base": self.time_base,
            "variable_frame_rate": self.variable_frame_rate,
            "codec": self.codec,
            "pixel_format": self.pixel_format,
            "channels": self.channels,
            "has_alpha": self.has_alpha,
            "sequence_pattern": seq.pattern if seq else None,
            "color_metadata": self.color_metadata,
            "extra": self.extra,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ConversionRequest:
    """User parameters, already parsed into types. Validate with ``validation.validate_request``."""

    input_path: Path
    project: str
    shot: str
    task: str
    version: str
    start_frame: int
    output_resolution: Resolution
    element: str = ""  # Element / Subtask folder, e.g. "water"
    # None = automatic directory from settings (default); a path = advanced manual override.
    output_directory: Path | None = None
    projects_root: str | None = None  # overrides the settings value for this OS
    input_colorspace: str = config.DEFAULT_INPUT_COLORSPACE
    output_colorspace: str = config.DEFAULT_OUTPUT_COLORSPACE
    ocio_config: str | None = None
    resize_mode: ResizeMode = ResizeMode.FIT
    overwrite: bool = False
    dry_run: bool = False
    accept_inferred_colorspace: bool = False
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
