"""FFprobe-based video inspection.

``parse_ffprobe`` is pure (testable with recorded JSON); ``probe_video`` runs FFprobe.
Subprocesses always use argument lists (never ``shell=True``) so paths with spaces work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from shot2exr import config
from shot2exr.errors import DependencyError, InputError
from shot2exr.models import Resolution, SourceInfo, SourceType

_UNSET = {None, "", "unknown", "unspecified", "reserved", "N/A"}
_ALPHA_PIX_FMT = re.compile(r"^(yuva|gbrap|ya\d)|rgba|bgra|argb|abgr")
_RGB_PIX_FMT = re.compile(r"^(gbr|rgb|bgr|argb|abgr|x?rgb|x?bgr)")
COLOR_KEYS = ("color_primaries", "color_transfer", "color_space", "color_range", "chroma_location")


def resolve_executable(name: str, override: str | os.PathLike | None = None) -> Path:
    """Find ``ffmpeg``/``ffprobe``: explicit override, then PATH (the conda env puts them there)."""
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        found = shutil.which(str(override))
        if found:
            return Path(found)
        raise DependencyError(f"{name} not found at {str(override)!r}.")
    found = shutil.which(name)
    if not found:
        raise DependencyError(
            f"{name} was not found on PATH. Install it in the conda env "
            f"('conda install -c conda-forge ffmpeg') or pass --{name}-path."
        )
    return Path(found)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": config.FFPROBE_TIMEOUT_S}
    if os.name == "nt":  # don't flash a console window from the GUI on Windows
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, check=False, **kwargs)


def run_ffprobe(path: Path, ffprobe: Path) -> dict[str, Any]:
    cmd = [str(ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = _run(cmd)
    except subprocess.TimeoutExpired:
        raise InputError(f"FFprobe timed out reading {path}.") from None
    except OSError as exc:
        raise DependencyError(f"Could not run FFprobe ({ffprobe}): {exc}") from None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise InputError(f"FFprobe could not read {path}: {detail[-1] if detail else 'unknown error'}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InputError(f"FFprobe returned invalid JSON for {path}: {exc}") from None


def count_video_packets(path: Path, ffprobe: Path) -> int | None:
    """Exact demuxed packet count of the first video stream (one packet per frame for video codecs)."""
    cmd = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "json", str(path),
    ]
    try:
        proc = _run(cmd)
        streams = json.loads(proc.stdout or "{}").get("streams") or []
        return int(streams[0]["nb_read_packets"]) if proc.returncode == 0 and streams else None
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def parse_rational(value: str | None) -> Fraction | None:
    if value in _UNSET:
        return None
    try:
        frac = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return frac if frac > 0 else None


def _clean(value: Any) -> Any:
    return None if value in _UNSET else value


def _rotation(stream: dict[str, Any]) -> float | None:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return float(side["rotation"])
            except (TypeError, ValueError):
                return None
    rotate = (stream.get("tags") or {}).get("rotate")
    try:
        return float(rotate) if rotate is not None else None
    except ValueError:
        return None


def parse_ffprobe(data: dict[str, Any], path: Path) -> SourceInfo:
    """Turn FFprobe JSON into ``SourceInfo``. Frame count may be ``None`` (see ``probe_video``)."""
    streams = data.get("streams") or []
    # Attached pictures (cover art) are reported as video streams; skip them.
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        raise InputError(f"No video stream found in {path}.")
    st = video[0]
    fmt = data.get("format") or {}
    info = SourceInfo(path=path, source_type=SourceType.VIDEO)

    width, height = st.get("width"), st.get("height")
    if width and height:
        info.resolution = Resolution(int(width), int(height))
    else:
        info.errors.append("Video stream reports no resolution.")

    info.codec = st.get("codec_name")
    info.pixel_format = _clean(st.get("pix_fmt"))
    info.time_base = _clean(st.get("time_base"))
    r_rate = parse_rational(st.get("r_frame_rate"))
    avg_rate = parse_rational(st.get("avg_frame_rate"))
    rate = avg_rate or r_rate
    info.fps = f"{rate.numerator}/{rate.denominator}" if rate else None
    if r_rate and avg_rate:
        info.variable_frame_rate = r_rate != avg_rate
    if info.variable_frame_rate:
        info.warnings.append(
            f"Possible variable frame rate (r_frame_rate={st.get('r_frame_rate')}, "
            f"avg_frame_rate={st.get('avg_frame_rate')}). Every decoded frame will be kept; "
            "source timing is recorded in the report."
        )

    pix = info.pixel_format or ""
    info.has_alpha = bool(_ALPHA_PIX_FMT.search(pix)) if pix else None
    info.channels = ["R", "G", "B", "A"] if info.has_alpha else ["R", "G", "B"]

    nb_frames = st.get("nb_frames")
    if nb_frames not in _UNSET and str(nb_frames).isdigit() and int(nb_frames) > 0:
        info.frame_count, info.frame_count_method = int(nb_frames), "nb_frames"
    else:
        duration = _clean(st.get("duration")) or _clean(fmt.get("duration"))
        try:
            if duration is not None and rate:
                info.frame_count = max(1, round(float(duration) * rate))
                info.frame_count_method = "estimated"
        except ValueError:
            pass

    info.color_metadata = {k: _clean(st.get(k)) for k in COLOR_KEYS}
    info.color_metadata.update(
        pix_fmt=info.pixel_format,
        is_rgb_pixel_format=bool(_RGB_PIX_FMT.match(pix)) if pix else None,
        bits_per_raw_sample=_clean(st.get("bits_per_raw_sample")),
        codec_tag_string=_clean(st.get("codec_tag_string")),
        profile=_clean(st.get("profile")),
    )

    sar = _clean(st.get("sample_aspect_ratio"))
    rotation = _rotation(st)
    info.extra = {
        "container": _clean(fmt.get("format_name")),
        "duration_s": _clean(st.get("duration")) or _clean(fmt.get("duration")),
        "r_frame_rate": _clean(st.get("r_frame_rate")),
        "avg_frame_rate": _clean(st.get("avg_frame_rate")),
        "start_time": _clean(st.get("start_time")),
        "field_order": _clean(st.get("field_order")),
        "sample_aspect_ratio": sar,
        "rotation": rotation,
        "video_stream_index": st.get("index"),
        "video_stream_count": len(video),
        "stream_tags": st.get("tags") or {},
    }
    if len(video) > 1:
        info.warnings.append(f"{len(video)} video streams found; only the first (index {st.get('index')}) is used.")
    if sar and sar not in ("1:1", "0:1"):
        info.warnings.append(f"Non-square pixels (sample aspect ratio {sar}); output resolution is applied to stored pixels.")
    if rotation:
        info.warnings.append(f"Display rotation metadata of {rotation:g} degrees is present; frames are stored unrotated.")
    if info.extra["field_order"] not in (None, "progressive"):
        info.warnings.append(f"Interlaced source (field_order={info.extra['field_order']}); frames are not deinterlaced.")
    return info


def probe_video(path: Path, ffprobe_path: str | None = None, count_frames: bool = True) -> SourceInfo:
    ffprobe = resolve_executable("ffprobe", ffprobe_path)
    info = parse_ffprobe(run_ffprobe(path, ffprobe), path)
    info.extra["ffprobe"] = str(ffprobe)
    if count_frames and info.frame_count_method in (None, "estimated"):
        counted = count_video_packets(path, ffprobe)
        if counted:
            info.frame_count, info.frame_count_method = counted, "count_packets"
    timing = frame_timing(path, ffprobe)
    info.extra["timing"] = timing
    if timing and timing.get("constant") is False and not info.variable_frame_rate:
        info.variable_frame_rate = True
        info.warnings.append(
            "Frame durations vary (variable frame rate). Every decoded frame is kept once; "
            "source timing is recorded in the report."
        )
    if info.frame_count is None:
        info.errors.append("Could not determine the number of video frames.")
    elif info.frame_count_method == "estimated":
        info.warnings.append("Frame count is estimated from duration x frame rate; the exact count is verified during conversion.")
    return info


def tool_version(executable: Path) -> str | None:
    try:
        proc = _run([str(executable), "-version"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (proc.stdout or "").splitlines()[:1]
    return first[0] if first else None


def frame_timing(path: Path, ffprobe: Path) -> dict[str, Any] | None:
    """Presentation timing of the first video stream from packet timestamps (no decoding).

    Used for the report, mainly to document variable-frame-rate sources.
    """
    cmd = [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
           "-of", "csv=p=0", str(path)]
    try:
        proc = _run(cmd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    pts = sorted(float(v) for v in (proc.stdout or "").split() if v.strip() not in ("", "N/A"))
    if len(pts) < 2:
        return {"packets": len(pts), "first_pts_s": pts[0] if pts else None}
    deltas = [b - a for a, b in zip(pts, pts[1:])]
    return {
        "packets": len(pts),
        "first_pts_s": round(pts[0], 6),
        "last_pts_s": round(pts[-1], 6),
        "min_frame_duration_s": round(min(deltas), 6),
        "max_frame_duration_s": round(max(deltas), 6),
        "mean_frame_duration_s": round(sum(deltas) / len(deltas), 6),
        "constant": max(deltas) - min(deltas) < 1e-4,
    }
