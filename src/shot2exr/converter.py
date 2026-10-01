"""The shared engine used by both the CLI and the GUI.

Milestone 1: ``inspect_source`` (metadata only) and ``plan_conversion`` (the full dry run:
validation, colour resolution, naming, collision checks). Pixel conversion is Milestone 2+.
"""

from __future__ import annotations

import os
import platform
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shot2exr import TOOL_NAME, __version__, config, naming
from shot2exr.color_manager import ColorConfig, ColorPipeline, load_config, ocio_version
from shot2exr.colorspace_detector import PRIMARIES, detect_exr, gamut_of_interop, detect_video
from shot2exr.errors import ExitCode, InputError, OutputError, Shot2EXRError
from shot2exr.exr_reader import ExrHeader, oiio_version, read_frame, read_header
from shot2exr.exr_writer import write_frame
from shot2exr.media_probe import probe_video, resolve_executable
from shot2exr.models import (
    ColorDetection, ConversionRequest, DetectionState, Resolution, SourceInfo, SourceType,
)
from shot2exr.output_paths import OutputLocation, resolve_output_location
from shot2exr.report import STATUS_CANCELLED, STATUS_FAILED, STATUS_SUCCESS, build_report, report_path, write_report
from shot2exr.resize import ResizeGeometry, apply_geometry, compute_geometry
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
    stale = {report_path(out, plan.basename, st).name for st in (STATUS_FAILED, STATUS_CANCELLED)}
    leftovers = {n for n in existing if n.startswith(".") or n in stale}
    for name in sorted(stale & existing):
        plan.warnings.append(f"A previous attempt did not complete ({name}); it is ignored and removed on success.")
    existing -= leftovers
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
        try:
            plan.color_transforms = processing_steps(cfg, plan)[1]
        except Shot2EXRError as exc:
            plan.error(exc.exit_code, exc.message)
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


# --------------------------------------------------------------------------- conversion

class ConversionCancelled(Shot2EXRError):
    exit_code = ExitCode.CANCELLED


@dataclass
class ConversionResult:
    status: str  # success | failed | cancelled
    output_directory: Path
    report_path: Path | None
    frames_written: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def exit_code(self) -> ExitCode:
        if self.ok:
            return ExitCode.OK
        return ExitCode.CANCELLED if self.status == STATUS_CANCELLED else ExitCode.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "output_directory": str(self.output_directory),
                "report": str(self.report_path) if self.report_path else None, "frames_written": self.frames_written,
                "errors": self.errors, "warnings": self.warnings, "duration_s": self.duration_s}


ProgressCallback = Callable[[int, int, str], None]


def processing_steps(cfg: ColorConfig, plan: ConversionPlan) -> tuple[ColorPipeline, list[str]]:
    """The exact per-frame steps, shared by the dry run and the real conversion."""
    resizing = plan.geometry is not None and not plan.geometry.is_identity
    pipeline = ColorPipeline.build(cfg, plan.input_colorspace, plan.output_colorspace, resizing)
    steps = list(pipeline.steps)
    if resizing:
        steps.insert(1 if pipeline.pre is not None else 0,
                     f"resize (lanczos3, in '{pipeline.resize_space}'): {plan.geometry.describe()}")
    if not steps:
        steps = [f"none (input and output are both '{plan.output_colorspace}', no resize)"]
    return pipeline, steps


def _output_attributes(cfg: ColorConfig, plan: ConversionPlan) -> dict[str, Any]:
    """Colour metadata describing the *output* encoding (input metadata is never copied)."""
    entry = cfg.entry(plan.output_colorspace)
    attrs: dict[str, Any] = {
        "shot2exr:inputColorspace": plan.input_colorspace,
        "shot2exr:outputColorspace": plan.output_colorspace,
        "shot2exr:ocioConfig": cfg.name or cfg.source,
    }
    if entry and not entry.is_data and entry.interop_id:
        attrs["colorInteropID"] = entry.interop_id
        gamut = gamut_of_interop(entry.interop_id)
        if gamut:
            attrs["chromaticities"] = PRIMARIES[gamut]
    return attrs


def run_conversion(plan: ConversionPlan, cfg: ColorConfig, *, progress: ProgressCallback | None = None,
                   cancel: threading.Event | None = None) -> ConversionResult:
    """Convert every source frame, one at a time, into ``plan.location.directory``.

    Frames are written to a hidden staging directory and only moved into place after all frames
    are written and validated, so an interrupted run never leaves a complete-looking sequence.
    A failed or cancelled run removes its partial frames and writes ``*.conversion_report.FAILED.json``
    (or ``.CANCELLED.json``) instead of the normal report.
    """
    if not plan.ok:
        raise Shot2EXRError("The conversion plan has unresolved problems; run a dry run to see them.",
                            [e.message for e in plan.errors])
    src = plan.inspection.source
    if src.source_type is not SourceType.EXR_SEQUENCE:
        err = Shot2EXRError("Video conversion arrives in Milestone 3; only EXR sequences can be converted now.")
        err.exit_code = ExitCode.NOT_IMPLEMENTED
        raise err

    started = datetime.now(timezone.utc).astimezone()
    out_dir = Path(plan.location.directory)
    existed = out_dir.is_dir()
    token = uuid.uuid4().hex[:8]
    if existed:
        staging = out_dir / f".shot2exr-inprogress-{token}"
    else:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = out_dir.parent / f".{out_dir.name}.inprogress-{token}"
    target = plan.request.output_resolution
    names = plan.output_filenames()
    pipeline, transforms = processing_steps(cfg, plan)
    attrs = _output_attributes(cfg, plan)
    warnings = [w for w in plan.warnings if "will be created" not in w]
    written: list[str] = []
    status, errors = STATUS_FAILED, []
    interrupt: BaseException | None = None
    try:
        staging.mkdir(parents=False, exist_ok=False)
        total = len(names)
        for i, (src_file, name) in enumerate(zip(src.sequence.files, names)):
            if cancel is not None and cancel.is_set():
                raise ConversionCancelled("Conversion cancelled by the user.")
            frame = read_frame(src_file)
            px = ColorPipeline.apply(pipeline.pre, frame.pixels)
            if plan.geometry and not plan.geometry.is_identity:
                px = apply_geometry(px, plan.geometry)
            px = ColorPipeline.apply(pipeline.post, px)
            write_frame(staging / name, px, frame.channels,
                        {**attrs, "shot2exr:sourceFile": src_file.name, "shot2exr:sourceFrame": src.sequence.frames[i]})
            written.append(name)
            if progress:
                progress(i + 1, total, name)

        # Validate before anything becomes visible under the final names.
        present = sorted(p.name for p in staging.iterdir() if p.suffix.lower() == ".exr")
        if present != sorted(names):
            raise OutputError(f"Expected {len(names)} frames in staging, found {len(present)}.")
        for name in names:
            hdr = read_header(staging / name)
            if (hdr.width, hdr.height) != (target.width, target.height):
                raise OutputError(f"{name} is {hdr.width}x{hdr.height}, expected {target}.")
        status = STATUS_SUCCESS
    except ConversionCancelled as exc:
        status, errors = STATUS_CANCELLED, [exc.message]
    except Shot2EXRError as exc:
        errors = [str(exc)]
    except KeyboardInterrupt as exc:
        status, errors, interrupt = STATUS_CANCELLED, ["Interrupted (Ctrl+C)."], exc
    except Exception as exc:  # noqa: BLE001 - report every failure, never leave half a sequence
        errors = [f"{type(exc).__name__}: {exc}"]

    finished = datetime.now(timezone.utc).astimezone()
    report = build_report(plan, status=status, started=started, finished=finished, transforms=transforms,
                          frames_written=len(written) if status == STATUS_SUCCESS else 0,
                          files=names if status == STATUS_SUCCESS else [], errors=errors, warnings=warnings,
                          validation_passed=status == STATUS_SUCCESS)
    if status == STATUS_SUCCESS:
        rpath = report_path(out_dir, plan.basename, STATUS_SUCCESS)
        write_report(staging / rpath.name, report)
        try:
            if existed:
                for item in staging.iterdir():
                    os.replace(item, out_dir / item.name)
                staging.rmdir()
            else:
                os.rename(staging, out_dir)
        except OSError as exc:
            status, errors = STATUS_FAILED, [f"Could not move the finished frames into {out_dir}: {exc}"]
            report["general"]["status"] = status
            report["validation"].update(result="failed", errors=errors)
    if status != STATUS_SUCCESS:
        shutil.rmtree(staging, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        report["output"]["partial_frames_removed"] = len(written)
        rpath = write_report(report_path(out_dir, plan.basename, status), report)
    else:
        for stale in (STATUS_FAILED, STATUS_CANCELLED):
            report_path(out_dir, plan.basename, stale).unlink(missing_ok=True)
    result = ConversionResult(status, out_dir, rpath, len(written) if status == STATUS_SUCCESS else 0, errors,
                              warnings, report["general"]["processing_duration_s"])
    if interrupt is not None:
        raise interrupt
    return result


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
