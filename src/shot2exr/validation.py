"""Parameter parsing and validation shared by the CLI and GUI."""

from __future__ import annotations

import re
from pathlib import Path

from shot2exr import naming
from shot2exr.errors import ValidationError
from shot2exr.models import ConversionRequest, Resolution, ResizeMode

_RES_RE = re.compile(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$")
MAX_DIMENSION = 32768


def parse_resolution(value: str | Resolution) -> Resolution:
    """``"2048x1152"`` -> ``Resolution(2048, 1152)``."""
    if isinstance(value, Resolution):
        res = value
    else:
        match = _RES_RE.match(str(value or ""))
        if not match:
            raise ValidationError(f"Resolution {value!r} is invalid: expected WIDTHxHEIGHT, e.g. 2048x1152.")
        res = Resolution(int(match.group(1)), int(match.group(2)))
    if not (1 <= res.width <= MAX_DIMENSION and 1 <= res.height <= MAX_DIMENSION):
        raise ValidationError(f"Resolution {res} is out of range (1..{MAX_DIMENSION} per side).")
    return res


def parse_resize_mode(value: str | ResizeMode) -> ResizeMode:
    try:
        return ResizeMode(str(value.value if isinstance(value, ResizeMode) else value).strip().lower())
    except ValueError:
        choices = ", ".join(m.value for m in ResizeMode)
        raise ValidationError(f"Resize mode {value!r} is invalid: choose one of {choices}.") from None


def validate_request(req: ConversionRequest) -> ConversionRequest:
    """Check every user parameter, collecting all problems into one ``ValidationError``.

    Returns a copy-equivalent request with normalized fields (version ``v001`` etc.).
    Source and colour checks need I/O and happen in ``converter.plan_conversion``.
    """
    problems: list[str] = []

    def check(fn, *args):
        try:
            return fn(*args)
        except ValidationError as exc:
            problems.append(exc.message)
            return None

    project = check(naming.validate_token, req.project, "Project")
    shot = check(naming.validate_token, req.shot, "Shot")
    task = check(naming.validate_token, req.task, "Task")
    version = check(naming.normalize_version, req.version)
    start = check(naming.validate_frame_number, req.start_frame)
    resolution = check(parse_resolution, req.output_resolution)
    mode = check(parse_resize_mode, req.resize_mode)
    element = check(naming.validate_token, req.element, "Element")

    if not str(req.input_path or "").strip():
        problems.append("Input path is required.")
    if not (req.input_colorspace or "").strip():
        problems.append("Input colour space is required (use 'auto' for detection).")
    if not (req.output_colorspace or "").strip() or req.output_colorspace.strip().lower() == "auto":
        problems.append("Output colour space must be an explicit OCIO colour space name.")

    if problems:
        raise ValidationError("Invalid conversion parameters:", problems)

    return ConversionRequest(
        input_path=Path(req.input_path).expanduser(),
        project=project,
        shot=shot,
        task=task,
        version=version,
        start_frame=start,
        output_resolution=resolution,
        element=element,
        output_directory=Path(req.output_directory).expanduser() if str(req.output_directory or "").strip() else None,
        projects_root=(str(req.projects_root).strip() or None) if req.projects_root else None,
        input_colorspace=req.input_colorspace.strip(),
        output_colorspace=req.output_colorspace.strip(),
        ocio_config=(req.ocio_config or None),
        resize_mode=mode,
        overwrite=bool(req.overwrite),
        dry_run=bool(req.dry_run),
        accept_inferred_colorspace=bool(req.accept_inferred_colorspace),
        ffmpeg_path=req.ffmpeg_path or None,
        ffprobe_path=req.ffprobe_path or None,
    )
