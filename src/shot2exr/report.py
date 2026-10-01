"""Conversion report (JSON) for traceability. Unknown values are JSON ``null``, never invented."""

from __future__ import annotations

import json
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shot2exr import TOOL_NAME, __version__, config, naming

if TYPE_CHECKING:
    from shot2exr.converter import ConversionPlan

STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED = "success", "failed", "cancelled"


def report_path(directory: Path, basename: str, status: str) -> Path:
    name = naming.report_filename(basename)
    if status != STATUS_SUCCESS:  # clearly differentiated, never mistaken for a completed conversion
        name = name.replace(".json", f".{status.upper()}.json")
    return Path(directory) / name


def _dependencies() -> dict[str, Any]:
    from shot2exr.color_manager import ocio_version
    from shot2exr.exr_reader import oiio_version

    try:
        import numpy

        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = None
    return {"python": platform.python_version(), "openimageio": oiio_version(), "opencolorio": ocio_version(), "numpy": numpy_version}


def build_report(plan: "ConversionPlan", *, status: str, started: datetime, finished: datetime,
                 transforms: list[str], frames_written: int, files: list[str], errors: list[str],
                 warnings: list[str], validation_passed: bool) -> dict[str, Any]:
    req, insp = plan.request, plan.inspection
    src, det = insp.source, insp.detection
    seq = src.sequence
    geom = plan.geometry
    return {
        "general": {
            "tool": TOOL_NAME,
            "tool_version": __version__,
            "timestamp": finished.isoformat(),
            "started_at": started.isoformat(),
            "operating_system": platform.platform(),
            "hostname": platform.node() or None,
            "processing_duration_s": round((finished - started).total_seconds(), 3),
            "status": status,
            "dependencies": _dependencies(),
        },
        "input": {
            "source_path": str(Path(src.path).resolve()),
            "source_type": src.source_type.value,
            "sequence_pattern": seq.pattern if seq else None,
            "original_resolution": str(src.resolution) if src.resolution else None,
            "original_frame_count": src.frame_count,
            "original_frame_range": list(src.frame_range) if src.frame_range else None,
            "fps": src.fps,
            "time_base": src.time_base,
            "variable_frame_rate": src.variable_frame_rate,
            "channels": src.channels,
            "original_color_metadata": src.color_metadata,
            "missing_frames": src.missing_frames,
        },
        "color_management": {
            "detected_colorspace": det.colorspace,
            "detection_state": det.state.value,
            "detection_explanation": det.explanation,
            "detection_evidence": det.evidence,
            "manual_override": plan.manual_override,
            "input_colorspace_origin": plan.input_colorspace_origin,
            "input_colorspace_used": plan.input_colorspace,
            "output_colorspace": plan.output_colorspace,
            "ocio_config_path": insp.ocio.get("source"),
            "ocio_config_origin": insp.ocio.get("origin"),
            "ocio_config_name": insp.ocio.get("name"),
            "ocio_config_cache_id": insp.ocio.get("cache_id"),
            "ocio_config_sha256": insp.ocio.get("file_sha256"),
            "transforms_performed": transforms,
        },
        "output": {
            "output_directory": str(plan.location.directory),
            "directory_origin": plan.location.origin,
            "projects_root": plan.location.projects_root,
            "element": req.element,
            "filename_pattern": naming.output_pattern(plan.basename),
            "start_frame": plan.frame_range[0] if plan.frame_range else None,
            "end_frame": plan.frame_range[1] if plan.frame_range else None,
            "exported_frame_count": frames_written,
            "resolution": str(req.output_resolution),
            "resize_mode": req.resize_mode.value,
            "resize": geom.describe() if geom else None,
            "exr_bit_depth": "half (16-bit float)" if config.EXR_PIXEL_TYPE == "half" else config.EXR_PIXEL_TYPE,
            "compression": config.EXR_COMPRESSION,
            "alpha": bool(src.has_alpha),
            "files": files,
        },
        "validation": {
            "expected_frame_count": plan.frame_count,
            "actual_frame_count": frames_written,
            "errors": errors,
            "warnings": warnings,
            "result": "passed" if validation_passed else "failed",
        },
    }


def write_report(path: Path, data: dict[str, Any]) -> Path:
    """Write atomically (temporary file + rename) so a report is never half-written."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
