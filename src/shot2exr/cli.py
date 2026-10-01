"""Command-line interface: a thin layer over ``converter``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from shot2exr import TOOL_NAME, __version__, config, shot_memory
from shot2exr.color_manager import load_config
from shot2exr.converter import ConversionPlan, Inspection, environment_info, inspect_source, plan_conversion, run_conversion
from shot2exr.errors import ExitCode, Shot2EXRError, describe_os_error
from shot2exr.models import ConversionRequest, ResizeMode
from shot2exr.settings import load_settings
from shot2exr.validation import parse_resolution

REQUIRED_FOR_PLAN = ("project", "shot", "task", "element", "version", "start_frame", "resolution")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shot2exr",
        description="Convert MOV/MP4 or EXR sequences into pipeline-named, OCIO-converted EXR sequences.",
        epilog=f"Exit codes: " + ", ".join(f"{c.value}={c.name}" for c in ExitCode),
    )
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {__version__}")
    p.add_argument("-i", "--input", help="Source video (.mov/.mp4), EXR directory, or one frame of an EXR sequence.")
    p.add_argument("--project")
    p.add_argument("--shot")
    p.add_argument("--task")
    p.add_argument("--element", help="Element / subtask folder, e.g. water (the folder above the version directory).")
    p.add_argument("--version-number", "--vn", dest="version", metavar="VERSION",
                   help="Output version, e.g. 001 or v001. (--version prints the tool version.)")
    p.add_argument("--start-frame", type=int)
    p.add_argument("--resolution", help="Output WIDTHxHEIGHT, e.g. 2048x1152 (default: remembered for this shot).")
    p.add_argument("--input-colorspace", help="OCIO colour space or 'auto' (default: remembered for this shot, else auto).")
    p.add_argument("--output-colorspace",
                   help=f"Default: remembered for this shot, else {config.DEFAULT_OUTPUT_COLORSPACE}.")
    p.add_argument("--projects-root", help="Override the projects root from the settings file for this run.")
    p.add_argument("-o", "--output-dir", help="Advanced: write into this exact directory instead of the automatic project structure.")
    p.add_argument("--settings", help="Settings TOML (default: $SHOT2EXR_SETTINGS or the per-user settings file).")
    p.add_argument("--ocio-config", help="OCIO config path or ocio:// URI (default: $OCIO, else the built-in studio config).")
    p.add_argument("--resize-mode", choices=[m.value for m in ResizeMode],
                   help=f"Default: remembered for this shot, else {config.DEFAULT_RESIZE_MODE}.")
    p.add_argument("--no-recall", action="store_true",
                   help="Ignore the settings remembered for this project + shot (they are still saved after the run).")
    p.add_argument("--overwrite", action="store_true", help="Allow replacing existing output files.")
    p.add_argument("--dry-run", action="store_true", help="Validate and show what would be written; never writes files.")
    p.add_argument("--accept-inferred-colorspace", action="store_true", help="Confirm an INFERRED input colour space.")
    p.add_argument("--ffmpeg-path")
    p.add_argument("--ffprobe-path")
    p.add_argument("--inspect", action="store_true", help="Only inspect --input (metadata and colour detection).")
    p.add_argument("--list-colorspaces", action="store_true", help="List colour spaces of the active OCIO config.")
    p.add_argument("--env-info", action="store_true", help="Print dependency versions.")
    p.add_argument("--check-environment", action="store_true",
                   help="Test every native dependency (OIIO, OCIO, FFmpeg, Qt, settings); exit 6 if one is broken.")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    return p


def _version_arg_fixup(argv: list[str]) -> list[str]:
    """Accept the spec's ``--version 001`` form: ``--version`` followed by a value means the output version."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--version" and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            out += ["--version-number", argv[i + 1]]
            i += 2
            continue
        if arg.startswith("--version="):
            out.append("--version-number=" + arg.split("=", 1)[1])
        else:
            out.append(arg)
        i += 1
    return out


# Options a successful conversion remembers per project + shot: argparse name -> history field.
RECALLED = {"resolution": "resolution", "resize_mode": "resize_mode", "input_colorspace": "input_colorspace",
            "output_colorspace": "output_colorspace", "ocio_config": "ocio_config"}
BUILTIN_DEFAULTS = {"resize_mode": config.DEFAULT_RESIZE_MODE, "input_colorspace": config.DEFAULT_INPUT_COLORSPACE,
                    "output_colorspace": config.DEFAULT_OUTPUT_COLORSPACE}


def apply_recalled_settings(args: argparse.Namespace) -> list[str]:
    """Fill options left out on the command line: remembered for this shot, else the built-in default.

    Explicit options always win. Returns ``name=value`` for every remembered value used.
    """
    used: list[str] = []
    memo = None if args.no_recall else shot_memory.recall(args.project, args.shot)
    for attr, field in RECALLED.items():
        value = (memo or {}).get(field)
        if attr == "resize_mode" and value not in [m.value for m in ResizeMode]:
            value = None
        if getattr(args, attr) in (None, "") and value:
            setattr(args, attr, value)
            used.append(f"{attr.replace('_', '-')}={value}")
    for attr, default in BUILTIN_DEFAULTS.items():
        if getattr(args, attr) in (None, ""):
            setattr(args, attr, default)
    return used


# --------------------------------------------------------------------------- text output

def _kv(lines: list[str], key: str, value: Any) -> None:
    lines.append(f"  {key:<24}{'-' if value in (None, '', []) else value}")


def format_inspection(insp: Inspection) -> str:
    s, d = insp.source, insp.detection
    lines = ["SOURCE"]
    _kv(lines, "Path", s.path)
    _kv(lines, "Type", s.source_type.value)
    if s.sequence:
        _kv(lines, "Pattern", s.sequence.pattern)
    _kv(lines, "Resolution", s.resolution)
    count = s.frame_count if s.frame_count_exact else (f"~{s.frame_count} (estimated)" if s.frame_count else None)
    _kv(lines, "Frame count", count)
    _kv(lines, "Frame range", f"{s.frame_range[0]}-{s.frame_range[1]}" if s.frame_range else None)
    if s.missing_frames:
        from shot2exr.naming import format_frame_ranges
        _kv(lines, "Missing frames", format_frame_ranges(s.missing_frames))
    _kv(lines, "FPS / time base", f"{s.fps} / {s.time_base}" if s.fps else None)
    _kv(lines, "Codec / pixel format", f"{s.codec} / {s.pixel_format}" if s.codec or s.pixel_format else None)
    _kv(lines, "Channels", ",".join(s.channels))
    _kv(lines, "Alpha", s.has_alpha)
    lines.append("COLOR")
    _kv(lines, "OCIO config", f"{insp.ocio.get('name')} ({insp.ocio.get('origin')}: {insp.ocio.get('source')})")
    _kv(lines, "Detection state", d.state.value)
    _kv(lines, "Detected colour space", d.colorspace)
    _kv(lines, "Explanation", d.explanation)
    meta = {k: v for k, v in s.color_metadata.items() if v is not None}
    _kv(lines, "Source colour metadata", json.dumps(meta) if meta else None)
    if d.decode:
        _kv(lines, "Decode (pre-OCIO)", json.dumps({k: v for k, v in d.decode.items() if k != "assumed"}))
    for w in s.warnings:
        lines.append(f"WARNING: {w}")
    for e in s.errors:
        lines.append(f"ERROR: {e}")
    return "\n".join(lines)


def format_plan(plan: ConversionPlan) -> str:
    data = plan.to_dict()
    lines = [format_inspection(plan.inspection), "PLAN (dry run, nothing written)"] if plan.inspection else ["PLAN"]
    c, o = data["color"], data["output"]
    src = c["input_colorspace"] or "-"
    _kv(lines, "Input colour space", f"{src} ({c['input_colorspace_origin'] or 'unresolved'})")
    _kv(lines, "Output colour space", c["output_colorspace"])
    _kv(lines, "Transforms", "; ".join(c["transforms"]))
    _kv(lines, "Target resolution", f"{o['resolution']} ({o['resize_mode']})")
    _kv(lines, "Resize", o["resize"])
    _kv(lines, "Element", o["element"])
    _kv(lines, "Output directory", f"{o['directory']} ({o['directory_origin']})" if o["directory"] else None)
    _kv(lines, "Filename pattern", o["pattern"])
    _kv(lines, "Output frame range", f"{o['start_frame']}-{o['end_frame']} ({o['frame_count']} frames)" if o["end_frame"] is not None else None)
    _kv(lines, "EXR", f"{o['exr_pixel_type']} float, {o['exr_compression']}, alpha={o['alpha']}")
    _kv(lines, "Report", o["report"])
    m = o["review_movie"]
    _kv(lines, "Review movie", f"{m['file']} ({m['codec']}, {m['fps']} fps {m['fps_origin']}, "
                               f"{m['display']} / {m['view']}; review only)")
    if o["filenames_preview"]:
        lines.append("  Expected files:")
        lines += [f"    {n}" for n in o["filenames_preview"]]
    for w in data["warnings"]:
        if w not in plan.inspection.source.warnings:
            lines.append(f"WARNING: {w}")
    for e in plan.errors:
        if e.message not in plan.inspection.source.errors:
            lines.append(f"ERROR: {e.message}")
    lines.append("RESULT: OK, ready to convert" if plan.ok else f"RESULT: {len(plan.errors)} problem(s) must be fixed")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main

def _progress_printer():
    tty = sys.stderr.isatty()

    def show(done: int, total: int, name: str) -> None:
        if tty:
            print(f"\r  [{done:>{len(str(total))}}/{total}] {name}", end="" if done < total else "\n", file=sys.stderr, flush=True)
        elif done == total or done % max(1, total // 10) == 0:
            print(f"  [{done}/{total}] {name}", file=sys.stderr, flush=True)

    return show


def _emit(args, payload: dict[str, Any], text: str) -> None:
    print(json.dumps(payload, indent=2, default=str) if args.json else text)


def main(argv: list[str] | None = None) -> int:
    argv = _version_arg_fixup(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.env_info:
            info = environment_info()
            _emit(args, info, "\n".join(f"{k}: {v}" for k, v in info.items()))
            return ExitCode.OK
        if args.check_environment:
            from shot2exr import diagnostics

            checks = diagnostics.run_checks(ocio_config=args.ocio_config, ffmpeg_path=args.ffmpeg_path,
                                            ffprobe_path=args.ffprobe_path, settings_path=args.settings)
            _emit(args, diagnostics.summary(checks), diagnostics.format_checks(checks))
            return ExitCode.OK if diagnostics.summary(checks)["ok"] else ExitCode.DEPENDENCY
        if args.list_colorspaces:
            cfg = load_config(args.ocio_config)
            entries = cfg.colorspaces()
            payload = {"ocio": cfg.describe(), "colorspaces": [e.__dict__ for e in entries]}
            text = "\n".join(f"{e.name}" + ("  [display]" if e.is_display else "") for e in entries)
            _emit(args, payload, text)
            return ExitCode.OK
        if not args.input:
            parser.print_usage(sys.stderr)
            print("shot2exr: error: --input is required.", file=sys.stderr)
            return ExitCode.USAGE
        if args.inspect:
            insp = inspect_source(args.input, ocio_config=args.ocio_config, ffprobe_path=args.ffprobe_path)
            _emit(args, insp.to_dict(), format_inspection(insp))
            return ExitCode.OK

        recalled = apply_recalled_settings(args)
        if recalled:
            print(f"shot2exr: using settings remembered for {shot_memory.shot_key(args.project, args.shot)} "
                  f"({', '.join(recalled)}); pass the option to change it, or --no-recall.", file=sys.stderr)
        missing = [f"--{n.replace('_', '-')}" for n in REQUIRED_FOR_PLAN if getattr(args, n) in (None, "")]
        if missing:
            print(f"shot2exr: error: missing required option(s): {', '.join(missing)}", file=sys.stderr)
            return ExitCode.USAGE
        request = ConversionRequest(
            input_path=Path(args.input),
            project=args.project, shot=args.shot, task=args.task, version=args.version,
            start_frame=args.start_frame, output_resolution=parse_resolution(args.resolution),
            element=args.element, output_directory=Path(args.output_dir) if args.output_dir else None,
            projects_root=args.projects_root,
            input_colorspace=args.input_colorspace, output_colorspace=args.output_colorspace,
            ocio_config=args.ocio_config, resize_mode=ResizeMode(args.resize_mode),
            overwrite=args.overwrite, dry_run=args.dry_run,
            accept_inferred_colorspace=args.accept_inferred_colorspace,
            ffmpeg_path=args.ffmpeg_path, ffprobe_path=args.ffprobe_path,
        )
        settings = None if args.output_dir else load_settings(args.settings)
        cfg = load_config(args.ocio_config)
        plan = plan_conversion(request, cfg, settings=settings)
        if args.dry_run or not plan.ok:
            _emit(args, plan.to_dict(), format_plan(plan))
            return plan.exit_code
        if not args.json:
            print(format_plan(plan).replace("PLAN (dry run, nothing written)", "PLAN"))
        result = run_conversion(plan, cfg, progress=None if args.json else _progress_printer())
        if args.json:
            print(json.dumps({"plan": plan.to_dict(), "result": result.to_dict()}, indent=2, default=str))
        else:
            print(f"\n{result.status.upper()}: {result.frames_written} frame(s) in {result.output_directory}")
            for err in result.errors:
                print(f"ERROR: {err}", file=sys.stderr)
            if result.report_path:
                print(f"Report: {result.report_path}")
        return result.exit_code
    except Shot2EXRError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": {"code": exc.exit_code.name, "message": exc.message, "details": exc.problems}}, indent=2))
        else:
            print(f"shot2exr: error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("shot2exr: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - never end on a bare traceback
        message = describe_os_error(exc, "The operation") if isinstance(exc, OSError) else f"{type(exc).__name__}: {exc}"
        if args.json:
            print(json.dumps({"ok": False, "error": {"code": ExitCode.ERROR.name, "message": message, "details": []}}, indent=2))
        else:
            print(f"shot2exr: internal error: {message}", file=sys.stderr)
            if os.environ.get("SHOT2EXR_DEBUG"):
                traceback.print_exc()
            else:
                print("Set SHOT2EXR_DEBUG=1 to see the full traceback.", file=sys.stderr)
        return ExitCode.ERROR


if __name__ == "__main__":
    sys.exit(main())
