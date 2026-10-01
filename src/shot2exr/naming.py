"""Output naming: ``PROJECT_SHOT_TASK_vVERSION.FRAME.exr``.

All functions are pure and raise ``ValidationError`` on bad input, so the GUI preview,
the CLI and the converter share exactly the same rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from shot2exr import config
from shot2exr.errors import ValidationError

# Letters, digits, underscore and hyphen; must start with a letter or digit.
# Dots, slashes, spaces and ".." are rejected, which also blocks path traversal.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_VERSION_RE = re.compile(r"^[vV]?(\d+)$")


def validate_token(value: str, field: str) -> str:
    """Validate a project/shot/task token and return it stripped."""
    if value is None:
        raise ValidationError(f"{field} is required.")
    token = str(value).strip()
    if not token:
        raise ValidationError(f"{field} is required.")
    if len(token) > config.MAX_TOKEN_LENGTH:
        raise ValidationError(f"{field} is longer than {config.MAX_TOKEN_LENGTH} characters.")
    if not _TOKEN_RE.match(token):
        raise ValidationError(
            f"{field} {token!r} is invalid: use only letters, digits, '_' and '-', "
            "starting with a letter or digit."
        )
    return token


def normalize_version(value: str | int) -> str:
    """``"1"``, ``"001"``, ``"v001"``, ``"V1"`` -> ``"v001"``. Longer numbers are kept intact."""
    text = str(value).strip() if value is not None else ""
    match = _VERSION_RE.match(text)
    if not match:
        raise ValidationError(f"Version {text!r} is invalid: expected digits, optionally prefixed with 'v' (e.g. 001 or v001).")
    number = int(match.group(1))
    return f"v{number:0{config.VERSION_MIN_DIGITS}d}"


def validate_frame_number(value: int, field: str = "Start frame") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationError(f"{field} {value!r} is not an integer.") from None
    if value < 0:
        raise ValidationError(f"{field} must be 0 or greater (got {value}).")
    if value > config.MAX_FRAME_NUMBER:
        raise ValidationError(f"{field} must not exceed {config.MAX_FRAME_NUMBER}.")
    return value


def format_frame(frame: int) -> str:
    """Zero-pad to at least four digits; longer numbers are never truncated."""
    return f"{frame:0{config.FRAME_MIN_DIGITS}d}"


def output_basename(project: str, shot: str, task: str, version: str | int) -> str:
    """``PROJ``, ``0010_020``, ``comp``, ``1`` -> ``PROJ_0010_020_comp_v001``."""
    return "_".join(
        (
            validate_token(project, "Project"),
            validate_token(shot, "Shot"),
            validate_token(task, "Task"),
            normalize_version(version),
        )
    )


def output_filename(basename: str, frame: int) -> str:
    return f"{basename}.{format_frame(frame)}{config.EXR_EXTENSION}"


def output_pattern(basename: str) -> str:
    return f"{basename}.{'#' * config.FRAME_MIN_DIGITS}{config.EXR_EXTENSION}"


def movie_filename(basename: str) -> str:
    """``PROJ_0010_020_comp_v001`` -> ``PROJ_0010_020_comp_v001.mov`` (the review movie)."""
    return f"{basename}{config.REVIEW_MOVIE_EXTENSION}"


def report_filename(basename: str) -> str:
    return f"{basename}.conversion_report.json"


def output_frame_range(start_frame: int, frame_count: int) -> tuple[int, int]:
    """Inclusive output range for ``frame_count`` frames starting at ``start_frame``."""
    if frame_count < 1:
        raise ValidationError("Frame count must be at least 1.")
    return start_frame, start_frame + frame_count - 1


def output_filenames(basename: str, start_frame: int, frame_count: int) -> Iterator[str]:
    """Lazily yield every output filename (source frame order -> sequential output frames)."""
    for i in range(frame_count):
        yield output_filename(basename, start_frame + i)


def format_frame_ranges(frames: Iterable[int]) -> str:
    """``[1003, 1004, 1005, 1009]`` -> ``"1003-1005, 1009"``."""
    ordered = sorted(set(frames))
    if not ordered:
        return ""
    parts: list[str] = []
    start = prev = ordered[0]
    for f in ordered[1:]:
        if f == prev + 1:
            prev = f
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = f
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)
