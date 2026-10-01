# Shot2EXR

Converts a MOV/MP4 video or an existing EXR sequence into a correctly named, OCIO-converted
EXR sequence (`PROJECT_SHOT_TASK_vVVV.FRAME.exr`), a ProRes review movie (`PROJECT_SHOT_TASK_vVVV.mov`) and a
JSON conversion report.
One engine, two front ends: a PySide6 GUI (`shot2exr-gui`) and a CLI (`shot2exr`).

> **Status: Milestone 4.** EXR sequence -> EXR and MOV/MP4 -> EXR conversion work end to end in GUI
> and CLI (FFmpeg decode, OCIO colour conversion, FIT/FILL/STRETCH resize, half-float ZIP EXRs, JSON report).
> Standalone bundles for Rocky Linux 9 and Windows are built and tested in CI; see [INSTALL.md](INSTALL.md).

## Install

[INSTALL.md](INSTALL.md) covers the standalone bundle (no Python or conda needed), the conda
development setup on Rocky Linux 9 and Windows, the desktop libraries the GUI needs on a minimal
Rocky 9 install, and troubleshooting. Quick start from source:

```bash
conda env create -f environment.yml && conda activate shot2exr
python -m pip install -e . --no-deps
shot2exr --check-environment      # tests OIIO, OCIO, FFmpeg, Qt and settings; exit 6 if one is broken
```

## Launch

| | Rocky Linux 9 | Windows |
|---|---|---|
| GUI | `shot2exr-gui` | `shot2exr-gui` (no console window) |
| GUI fallback | `python -m shot2exr.gui` | `python -m shot2exr.gui` |
| CLI | `shot2exr --help` | `shot2exr --help` |
| CLI fallback | `python -m shot2exr --help` | `python -m shot2exr --help` |

## Output directory structure

Output goes into an automatically built version directory (nothing to type per shot):

```
{projects_root}/{ProjectFolder}/VFX/{PROJ}_{SEQ}/{PROJ}_{SHOT}/Tasks/{TaskFolder}/ComfyUI/{element}/{PROJ}_{SHOT}_{task}_v{VER}/
P:\Projects\MyProject\VFX\PROJ_0010\PROJ_0010_020\Tasks\Compositing\ComfyUI\fx\PROJ_0010_020_comp_v001\
```

`SEQ` is the first `_` part of the shot (`0010_020` -> `0010`, zeros kept). Roots and code -> folder
mappings live in a TOML settings file. Defaults ship inside the package (`src/shot2exr/default_settings.toml`);
your overrides go in the user file, which tables are merged into key by key:

| OS | User settings file |
|---|---|
| Rocky Linux 9 | `~/.config/shot2exr/settings.toml` (or `$XDG_CONFIG_HOME/shot2exr/settings.toml`) |
| Windows | `%APPDATA%\Shot2EXR\settings.toml` |

`SHOT2EXR_SETTINGS=/path/settings.toml` (or `--settings`) points at a shared studio file instead.

```toml
[paths.windows]
projects_root = "P:/Projects"

[paths.linux]
projects_root = "/your/production/mount"   # empty by default: Shot2EXR refuses to guess

[projects]
PROJ = "MyProject"

[tasks]
comp = "Compositing"
```

The GUI's *Settings...* button edits the projects root for the current OS. Add projects and tasks to the
file without touching code. The projects root must already exist (mounted). Dry runs never create directories,
and a non-empty existing version directory is refused unless overwrite is enabled.
`--output-dir` (GUI: *Manual output directory override*) is an advanced escape hatch that writes into an exact directory.

## CLI

```bash
shot2exr --inspect --input "/source/clip.mov"          # metadata + colour detection only

shot2exr --input "/source/clip.mov" --project PROJ --shot 0010_020 --task comp --element fx \
  --version 001 --start-frame 1009 --resolution 2048x1152 \
  --input-colorspace auto --output-colorspace ACEScg --dry-run
```

Other options: `--projects-root`, `--output-dir`, `--settings`, `--ocio-config`, `--resize-mode fit|fill|stretch`, `--overwrite`, `--no-recall`,
`--accept-inferred-colorspace`, `--ffmpeg-path`, `--ffprobe-path`, `--list-colorspaces`,
`--env-info`, `--check-environment`, `--json` (machine-readable output). On Windows PowerShell use a backtick `` ` ``
instead of `\` for line continuation.

An EXR directory containing several sequences is rejected; pass the path of one frame
(e.g. `/source/plate.1001.exr`) to choose that sequence.

Exit codes: 0 OK, 1 internal error, 2 invalid parameters, 3 source problem, 4 colour space
(unknown / needs confirmation / bad OCIO config), 5 output problem (e.g. files exist),
6 missing dependency, 7 not implemented, 8 settings problem (no projects root,
unmapped project/task), 9 cancelled.

## What a conversion does

Per frame, one frame in memory at a time: read EXR (display window, RGB or RGBA) -> OCIO input -> scene-linear
working space (only if the input is not already scene-linear and a resize is needed) -> Lanczos-3 resize
(premultiplied, unclamped: negatives and highlights survive) -> OCIO -> output space (alpha is
un-premultiplied around colour transforms) -> half-float ZIP EXR with `colorInteropID`/`chromaticities` of the
*output* space. Frames are written to a hidden staging folder and moved into place only after all of them are
written and validated, then `PROJECT_SHOT_TASK_vVVV.conversion_report.json` is written. A failed or cancelled run
removes its partial frames and leaves `...conversion_report.FAILED.json` / `.CANCELLED.json` instead.

### Review movie

Every conversion also writes `PROJECT_SHOT_TASK_vVVV.mov` in the same version folder. It is a **review proxy
only**, never pipeline data: the EXRs are the deliverable and are never touched by the review transform.

* Codec: ProRes 422 HQ (FFmpeg `prores_ks`, 10-bit 4:2:2), tagged BT.709. Odd sizes are padded by one pixel.
* Look: OCIO display/view transform from the output colour space to *Rec.1886 Rec.709 - Display* with the
  config's default view (falls back to the config's default display). Data spaces are shown as-is. Alpha is
  not kept: premultiplied frames appear composited over black.
* Frame rate: the source's for video input; 24 fps for EXR input (the dry run warns).
* It is encoded alongside the frames, inside the same staging folder, and its frame count and size are checked
  with FFprobe. If it fails, the whole conversion fails and no frames are kept. An existing `.mov` counts as an
  output collision (overwrite protection), and a dry run only shows its planned name.
* The report's `output.review_movie` block records the file, codec, fps (and where it came from), display,
  view and frame count.

### Remembered settings per shot

After a successful conversion (GUI or CLI), the resolution, resize mode, input and output colour spaces and
OCIO config are saved for that project + shot. A dry run, a cancelled or a failed conversion saves nothing. The
next conversion of the same shot, with any task, starts from them. Element, version and start frame are never
remembered. Project and shot are matched ignoring case and surrounding spaces (`proj` finds `PROJ`).

* CLI: options you leave out come from memory (a note on stderr lists them); options you pass always win.
  `--no-recall` ignores the memory for one run. With a remembered resolution, `--resolution` can be omitted.
* GUI: as soon as Project + Shot name a remembered shot, those fields are loaded (replacing whatever was there)
  and a note appears under *Shot information*. Changes you make afterwards are kept until the shot changes.
  The log shows the file's location at start-up, where each conversion saved its settings, and any problem
  reading or writing the file. The CLI prints the same on stdout/stderr.
* The file is per user (never in the repository or the project folders); `$SHOT2EXR_HISTORY` overrides it:

| OS | Remembered settings file |
|---|---|
| Rocky Linux 9 | `~/.config/shot2exr/shot_history.json` (or `$XDG_CONFIG_HOME/shot2exr/shot_history.json`) |
| Windows | `%APPDATA%\Shot2EXR\shot_history.json` |

Delete the file (or an entry in it) to forget remembered settings.

## Colour management

* OCIO config: `--ocio-config` > `$OCIO` > OCIO's built-in ACES studio config (`ocio://studio-config-latest`).
* Input colour space `auto` reports **DETECTED**, **INFERRED** (requires `--accept-inferred-colorspace`
  or the GUI checkbox) or **UNKNOWN** (requires a manual choice). Manual choices always win and are recorded.
* Video: FFmpeg only undoes the YUV encoding (explicit matrix and range, to full-range R'G'B', 16-bit);
  transfer and primaries are left to OCIO so nothing is applied twice. Every decoded frame is kept in
  order (`-fps_mode passthrough`, no frame dropping or duplication, also for VFR), and the decoded count
  must match the probed count or the conversion fails without leaving frames behind. Straight alpha
  (e.g. ProRes 4444) is premultiplied for EXR. The report records packet timing, the decode settings
  and the exact FFmpeg command.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest      # PowerShell: $env:QT_QPA_PLATFORM="offscreen"; python -m pytest
shot2exr-gui --self-test                        # opens the main window, closes it, exits 0
python tools/benchmark.py --frames 48           # seconds per frame and peak memory (not a test)
python packaging/build.py --test                # standalone bundle + distribution smoke test
```
Test media (EXR, MOV/MP4) is generated on the fly; nothing large is committed. `tests/test_end_to_end.py`
runs the installed `shot2exr` / `shot2exr-gui` commands as separate processes.
