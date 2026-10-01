"""The shared engine used by both the CLI and the GUI.

Milestone 1: ``inspect_source`` (metadata only) and ``plan_conversion`` (the full dry run:
validation, colour resolution, naming, collision checks). Pixel conversion is Milestone 2+.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shot2exr import TOOL_NAME, __version__, config, naming
from shot2exr.color_manager import ColorConfig, load_config, ocio_version
from shot2exr.colorspace_detector import detect_exr, detect_video
from shot2exr.errors import ExitCode, InputError, Shot2EXRError
from shot2exr.exr_reader import ExrHeader, oiio_version, read_header
from shot2exr.media_probe import probe_video, resolve_executable
from shot2exr.models import (
    ColorDetection, ConversionRequest, DetectionState, Resolution, SourceInfo, SourceType,
)
from shot2exr.output_paths import OutputLocation, resolve_output_location
from shot2exr.resize import ResizeGeometry, compute_geometry
from shot2exr.sequence_detector import find_sequence
from shot2exr.settings import Settings, load_settings, user_settings_path
from shot2exr.validation import validate_request


@dataclass
class Inspection:
    source: SourceInfo
    detection: ColorDetection
    ocio: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source.to_dict(), "color_detection": self.detection.to_dict(), "ocio": self.ocio}


def detect_source_type(path: Path) -> SourceType:
    if not path.exists():
        raise InputError(f"Input path does not exist: {path}")
    if path.is_dir():
        return SourceType.EXR_SEQUENCE
    suffix = path.suffix.lower()
    if suffix in config.VIDEO_EXTENSIONS:
        return SourceType.VIDEO
    if suffix == config.EXR_EXTENSION:
        return SourceType.EXR_SEQUENCE
    supported = ", ".join(sorted(config.VIDEO_EXTENSIONS | {config.EXR_EXTENSION}))
    raise InputError(f"Unsupported input extension {suffix!r} ({path.name}); supported: {supported} or an EXR directory.")


def _inspect_exr(path: Path) -> tuple[SourceInfo, list[dict[str, Any]], Path]:
    seq, warnings = find_sequence(path)
    info = SourceInfo(path=path, source_type=SourceType.EXR_SEQUENCE, sequence=seq)
    info.warnings.extend(warnings)
    info.errors.extend(seq.issues)
    info.frame_count, info.frame_count_method = seq.count, "files"
    info.frame_range = (seq.first, seq.last)
    info.missing_frames = seq.missing_frames
    if info.missing_frames:
        info.errors.append(
            f"{len(info.missing_frames)} missing frame(s): {naming.format_frame_ranges(info.missing_frames)}. "
            "Resolve the gaps before converting."
        )

    # Headers only (no pixels), one frame at a time: cheap and memory-bounded.
    first: ExrHeader | None = None
    color_attrs: list[dict[str, Any]] = []
    corrupt, mismatched = [], []
    for frame, file in zip(seq.frames, seq.files):
        try:
            hdr = read_header(file)
        except InputError as exc:
            corrupt.append(f"{frame}: {exc.message}")
            continue
        color_attrs.append(hdr.color_attributes)
        if first is None:
            first = hdr
        elif (hdr.width, hdr.height, hdr.channels, hdr.pixel_type) != (first.width, first.height, first.channels, first.pixel_type):
            mismatched.append(frame)
    if corrupt:
        info.errors.append(f"{len(corrupt)} unreadable/corrupt frame(s): " + "; ".join(corrupt[:5]) + (" ..." if len(corrupt) > 5 else ""))
    if mismatched:
        info.errors.append(
            "Frames differ from the first frame in resolution, channels or pixel type: "
            + naming.format_frame_ranges(mismatched)
        )
    if first is None:
        raise InputError(f"No readable EXR frame in {seq.pattern}.", info.errors)

    info.resolution = Resolution(first.width, first.height)
    info.channels = first.channels
    info.has_alpha = first.has_alpha
    info.pixel_format = first.pixel_type
    info.codec = first.compression
    info.color_metadata = first.color_attributes
    x, y, w, h = first.data_window
    info.extra = {
        "pattern": seq.pattern,
        "padding": seq.padding,
        "data_window": [x, y, w, h],
        "display_window": [first.width, first.height],
        "subimages": first.subimages,
    }
    if (x, y, w, h) != (0, 0, first.width, first.height):
        info.warnings.append(f"Data window {w}x{h}+{x}+{y} differs from the display window {info.resolution}.")
    if first.subimages > 1:
        info.warnings.append(f"Multi-part EXR ({first.subimages} parts); only the first part is used.")
    extra_ch = [c for c in first.channels if c not in ("R", "G", "B", "A")]
    if extra_ch:
        info.warnings.append(f"Extra channels are ignored: {', '.join(extra_ch[:8])}{' ...' if len(extra_ch) > 8 else ''}.")
    return info, color_attrs, seq.files[0]


def inspect_source(path: str | os.PathLike, cfg: ColorConfig | None = None, *,
                   ocio_config: str | None = None, ffprobe_path: str | None = None) -> Inspection:
    """Read source metadata and detect its colour space. Never writes anything."""
    path = Path(path).expanduser()
    if cfg is None:
        cfg = load_config(ocio_config)
    stype = detect_source_type(path)
    if stype is SourceType.VIDEO:
        source = probe_video(path, ffprobe_path)
        height = source.resolution.height if source.resolution else None
        detection = detect_video(source.color_metadata, cfg, height)
    else:
        source, attrs, sample = _inspect_exr(path)
        detection = detect_exr(attrs, cfg, sample)
    return Inspection(source, detection, cfg.describe())


# --------------------------------------------------------------------------- planning

@dataclass
class PlanIssue:
    code: ExitCode
    message: str


@dataclass
class ConversionPlan:
    request: ConversionRequest
    inspection: Inspection | None
    basename: str
    location: OutputLocation | None = None  # resolved version directory (None if it could not be built)
    input_colorspace: str | None = None
    input_colorspace_origin: str | None = None  # detected | inferred_confirmed | manual
    manual_override: bool = False
    output_colorspace: str | None = None
    color_transforms: list[str] = field(default_factory=list)
    frame_count: int | None = None
    frame_range: tuple[int, int] | None = None
    geometry: ResizeGeometry | None = None
    collisions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[PlanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def exit_code(self) -> ExitCode:
        return self.errors[0].code if self.errors else ExitCode.OK

    def error(self, code: ExitCode, message: str) -> None:
        self.errors.append(PlanIssue(code, message))

    def output_filenames(self) -> list[str]:
        if not self.frame_count:
            return []
        return list(naming.output_filenames(self.basename, self.request.start_frame, self.frame_count))

    def to_dict(self) -> dict[str, Any]:
        req = self.request
        names = self.output_filenames()
        preview = names if len(names) <= 6 else names[:3] + ["..."] + names[-3:]
        return {
            "tool": {"name": TOOL_NAME, "version": __version__},
            "dry_run": True,
            "ok": self.ok,
            "input": self.inspection.to_dict() if self.inspection else None,
            "color": {
                "detection_state": self.inspection.detection.state.value if self.inspection else None,
                "detected_colorspace": self.inspection.detection.colorspace if self.inspection else None,
                "requested_input_colorspace": req.input_colorspace,
                "input_colorspace": self.input_colorspace,
                "input_colorspace_origin": self.input_colorspace_origin,
                "manual_override": self.manual_override,
                "output_colorspace": self.output_colorspace,
                "transforms": self.color_transforms,
            },
            "output": {
                "directory": str(self.location.directory) if self.location else None,
                "directory_origin": self.location.origin if self.location else None,
                "projects_root": self.location.projects_root if self.location else None,
                "element": req.element,
                "pattern": naming.output_pattern(self.basename),
                "report": naming.report_filename(self.basename),
                "start_frame": self.frame_range[0] if self.frame_range else req.start_frame,
                "end_frame": self.frame_range[1] if self.frame_range else None,
                "frame_count": self.frame_count,
                "filenames_preview": preview,
                "resolution": str(req.output_resolution),
                "resize_mode": req.resize_mode.value,
                "resize": self.geometry.describe() if self.geometry else None,
                "exr_pixel_type": config.EXR_PIXEL_TYPE,
                "exr_compression": config.EXR_COMPRESSION,
                "alpha": self.inspection.source.has_alpha if self.inspection else None,
                "overwrite": req.overwrite,
                "collisions": self.collisions[:20],
                "collision_count": len(self.collisions),
            },
            "warnings": self.warnings,
            "errors": [{"code": e.code.name, "message": e.message} for e in self.errors],
        }


def _resolve_input_colorspace(plan: ConversionPlan, cfg: ColorConfig) -> None:
    req, det = plan.request, plan.inspection.detection
    if req.input_colorspace.lower() != "auto":
        name = cfg.find(req.input_colorspace)
        if not name:
            plan.error(ExitCode.COLORSPACE, f"Input colour space {req.input_colorspace!r} does not exist in the active OCIO config.")
            return
        plan.input_colorspace, plan.input_colorspace_origin = name, "manual"
        plan.manual_override = det.colorspace is not None and det.colorspace != name
        if plan.manual_override:
            plan.warnings.append(f"Manual input colour space '{name}' overrides the {det.state.value} value '{det.colorspace}'.")
        return
    if det.state is DetectionState.DETECTED:
        plan.input_colorspace, plan.input_colorspace_origin = det.colorspace, "detected"
    elif det.state is DetectionState.INFERRED:
        if req.accept_inferred_colorspace:
            plan.input_colorspace, plan.input_colorspace_origin = det.colorspace, "inferred_confirmed"
            plan.warnings.append(f"Using INFERRED input colour space '{det.colorspace}' (confirmed by the user).")
        else:
            plan.error(
                ExitCode.COLORSPACE,
                f"Input colour space was INFERRED as '{det.colorspace}' and needs confirmation: "
                "re-run with --accept-inferred-colorspace, or set --input-colorspace explicitly.",
            )
    else:
        plan.error(ExitCode.COLORSPACE, "Input colour space is UNKNOWN: set --input-colorspace to a colour space of the active OCIO config.")


def _check_output(plan: ConversionPlan) -> None:
    req = plan.request
    out = Path(plan.location.directory)
    if out.exists() and not out.is_dir():
        plan.error(ExitCode.OUTPUT, f"Output path exists and is not a directory: {out}")
        return
    if not out.exists():
        root = plan.location.projects_root
        if root and not Path(root).is_dir():
            plan.error(ExitCode.OUTPUT, f"Projects root {root} does not exist or is not mounted; nothing will be created under it.")
            return
        parent = next((p for p in out.parents if p.exists()), None)
        if parent is None or not os.access(parent, os.W_OK):
            plan.error(ExitCode.OUTPUT, f"Output directory {out} does not exist and cannot be created (no writable parent).")
        else:
            plan.warnings.append(f"Output directory {out} does not exist and will be created when the conversion starts.")
        return
    if not os.access(out, os.W_OK):
        plan.error(ExitCode.OUTPUT, f"Output directory is not writable: {out}")
    try:
        existing = {e.name for e in os.scandir(out)}
    except OSError as exc:
        plan.error(ExitCode.OUTPUT, f"Cannot list output directory {out}: {exc}")
        return
    expected = set(plan.output_filenames())
    report = naming.report_filename(plan.basename)
    plan.collisions = sorted(expected & existing) + ([report] if report in existing else [])
    if plan.collisions and not req.overwrite:
        plan.error(ExitCode.OUTPUT, f"{len(plan.collisions)} output file(s) already exist (e.g. {plan.collisions[0]}); enable overwrite to replace them.")
    elif plan.collisions:
        plan.warnings.append(f"{len(plan.collisions)} existing output file(s) will be overwritten.")
    if plan.location.origin == "auto" and existing and not req.overwrite and not plan.collisions:
        plan.error(ExitCode.OUTPUT, f"Version directory {out} already exists and is not empty ({len(existing)} item(s)); "
                   "use a new version or enable overwrite.")
    stray = [n for n in existing if n.startswith(plan.basename + ".") and n.lower().endswith(".exr") and n not in expected]
    if stray:
        plan.warnings.append(f"{len(stray)} other frame(s) of {plan.basename} outside the new range already exist in the output directory.")


def plan_conversion(request: ConversionRequest, cfg: ColorConfig | None = None,
                    inspection: Inspection | None = None, settings: Settings | None = None) -> ConversionPlan:
    """Validate everything a conversion needs and describe what it would do. Writes nothing.

    Raises ``ValidationError`` for bad parameters and ``Shot2EXRError`` for an unreadable
    source/config; all other problems are collected in ``plan.errors``.
    """
    req = validate_request(request)
    cfg = cfg or load_config(req.ocio_config)
    inspection = inspection or inspect_source(req.input_path, cfg, ffprobe_path=req.ffprobe_path)
    plan = ConversionPlan(req, inspection, naming.output_basename(req.project, req.shot, req.task, req.version))
    try:
        if req.output_directory is None and settings is None:
            settings = load_settings()
        plan.location = resolve_output_location(
            req.project, req.shot, req.task, req.element, req.version, settings=settings or Settings(),
            manual_directory=req.output_directory, projects_root=req.projects_root,
        )
    except Shot2EXRError as exc:
        plan.error(exc.exit_code, exc.message)
    src = inspection.source
    plan.warnings.extend(src.warnings)
    for msg in src.errors:
        plan.error(ExitCode.INPUT, msg)

    if src.frame_count:
        plan.frame_count = src.frame_count
        plan.frame_range = naming.output_frame_range(req.start_frame, src.frame_count)
    if src.resolution:
        plan.geometry = compute_geometry(src.resolution, req.output_resolution, req.resize_mode)

    _resolve_input_colorspace(plan, cfg)
    out_name = cfg.find(req.output_colorspace)
    if not out_name:
        plan.error(ExitCode.COLORSPACE, f"Output colour space {req.output_colorspace!r} does not exist in the active OCIO config.")
    else:
        plan.output_colorspace = out_name
        entry = cfg.entry(out_name)
        if entry and entry.is_display:
            plan.warnings.append(f"Output colour space '{out_name}' is display-referred; EXR plates are normally scene-linear.")
        if entry and entry.is_data:
            plan.warnings.append(f"Output colour space '{out_name}' is a data space: no colour conversion will be applied.")
    if plan.input_colorspace and plan.output_colorspace:
        if plan.input_colorspace == plan.output_colorspace:
            plan.color_transforms = [f"none (input and output are both '{plan.output_colorspace}')"]
        else:
            plan.color_transforms = [f"OCIO: '{plan.input_colorspace}' -> '{plan.output_colorspace}'"]
        if src.source_type is SourceType.VIDEO and inspection.detection.decode:
            d = inspection.detection.decode
            if d.get("yuv_to_rgb"):
                plan.color_transforms.insert(0, f"decode: YUV->RGB matrix {d['matrix']}, {d['range']} range")

    if src.source_type is SourceType.VIDEO:
        try:
            resolve_executable("ffmpeg", req.ffmpeg_path)
        except Shot2EXRError as exc:
            plan.error(exc.exit_code, exc.message)
    if plan.frame_count and plan.location:
        _check_output(plan)
    return plan


def environment_info() -> dict[str, Any]:
    """Dependency versions for reports and ``--version``."""
    info: dict[str, Any] = {
        "tool": f"{TOOL_NAME} {__version__}",
        "python": platform.python_version(),
        "os": platform.platform(),
        "openimageio": oiio_version(),
        "opencolorio": ocio_version(),
        "settings_file": str(user_settings_path()),
    }
    try:
        import PySide6

        info["pyside6"] = PySide6.__version__
    except ImportError:
        info["pyside6"] = None
    return info
