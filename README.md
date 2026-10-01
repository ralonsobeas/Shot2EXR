# Shot2EXR

Converts a MOV/MP4 video or an existing EXR sequence into a correctly named, OCIO-converted
EXR sequence (`PROJECT_SHOT_TASK_vVVV.FRAME.exr`) plus a JSON conversion report.
One engine, two front ends: a PySide6 GUI (`shot2exr-gui`) and a CLI (`shot2exr`).

> **Status: Milestone 2.** EXR sequence -> EXR conversion works end to end in GUI and CLI
> (OCIO colour conversion, FIT/FILL/STRETCH resize, half-float ZIP EXRs, JSON report).
> Video (MOV/MP4) input can be inspected and dry-run; converting it arrives in Milestone 3.

## Install (development)

All native dependencies (Qt, OpenImageIO, OpenColorIO, FFmpeg) come from **conda-forge**, so no
root/sudo or system packages are needed. Use Miniforge or micromamba.

### Rocky Linux 9 (bash)

```bash
# If conda isn't installed yet (user-level, no sudo):
curl -LO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p "$HOME/miniforge3" && source "$HOME/miniforge3/bin/activate"

cd Shot2EXR
conda env create -f environment.yml
conda activate shot2exr
python -m pip install -e . --no-deps
```

The GUI needs a desktop session (X11 or Wayland). On a minimal/server install the Qt xcb plugin
may need these libraries (ask an admin; `sudo` required):
`sudo dnf install mesa-libEGL mesa-libGL libxkbcommon-x11 xcb-util-cursor fontconfig`.
Qt picks Wayland or X11 automatically; to force X11 (e.g. Wayland issues): `export QT_QPA_PLATFORM=xcb`.

### Windows 10/11 (PowerShell, Miniforge Prompt)

```powershell
cd Shot2EXR
conda env create -f environment.yml
conda activate shot2exr
python -m pip install -e . --no-deps
```

`--no-deps` keeps pip from replacing the conda-forge builds. (Pip-only alternative, not the
supported path: `pip install -e .[pip,test]` with FFmpeg on PATH.)

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
T:\Volumes\Projects\GodOfTides\VFX\GOD_0046\GOD_0046_005\Tasks\MachineLearning\ComfyUI\water\GOD_0046_005_ml_v001\
```

`SEQ` is the first `_` part of the shot (`0046_005` -> `0046`, zeros kept). Roots and code -> folder
mappings live in a TOML settings file. Defaults ship inside the package (`src/shot2exr/default_settings.toml`);
your overrides go in the user file, which tables are merged into key by key:

| OS | User settings file |
|---|---|
| Rocky Linux 9 | `~/.config/shot2exr/settings.toml` (or `$XDG_CONFIG_HOME/shot2exr/settings.toml`) |
| Windows | `%APPDATA%\Shot2EXR\settings.toml` |

`SHOT2EXR_SETTINGS=/path/settings.toml` (or `--settings`) points at a shared studio file instead.

```toml
[paths.windows]
projects_root = "T:/Volumes/Projects"

[paths.linux]
projects_root = "/your/production/mount"   # empty by default: Shot2EXR refuses to guess

[projects]
GOD = "GodOfTides"

[tasks]
ml = "MachineLearning"
```

The GUI's *Settings...* button edits the projects root for the current OS. Add projects and tasks to the
file without touching code. The projects root must already exist (mounted). Dry runs never create directories,
and a non-empty existing version directory is refused unless overwrite is enabled.
`--output-dir` (GUI: *Manual output directory override*) is an advanced escape hatch that writes into an exact directory.

## CLI

```bash
shot2exr --inspect --input "/source/clip.mov"          # metadata + colour detection only

shot2exr --input "/source/clip.mov" --project GOD --shot 0046_005 --task ml --element water \
  --version 001 --start-frame 1009 --resolution 2048x1152 \
  --input-colorspace auto --output-colorspace ACEScg --dry-run
```

Other options: `--projects-root`, `--output-dir`, `--settings`, `--ocio-config`, `--resize-mode fit|fill|stretch`, `--overwrite`,
`--accept-inferred-colorspace`, `--ffmpeg-path`, `--ffprobe-path`, `--list-colorspaces`,
`--env-info`, `--json` (machine-readable output). On Windows PowerShell use a backtick `` ` ``
instead of `\` for line continuation.

An EXR directory containing several sequences is rejected; pass the path of one frame
(e.g. `/source/plate.1001.exr`) to choose that sequence.

Exit codes: 0 OK, 1 internal error, 2 invalid parameters, 3 source problem, 4 colour space
(unknown / needs confirmation / bad OCIO config), 5 output problem (e.g. files exist),
6 missing dependency, 7 not implemented yet (video conversion), 8 settings problem (no projects root,
unmapped project/task), 9 cancelled.

## What a conversion does

Per frame, one frame in memory at a time: read EXR (display window, RGB or RGBA) -> OCIO input -> scene-linear
working space (only if the input is not already scene-linear and a resize is needed) -> Lanczos-3 resize
(premultiplied, unclamped: negatives and highlights survive) -> OCIO -> output space (alpha is
un-premultiplied around colour transforms) -> half-float ZIP EXR with `colorInteropID`/`chromaticities` of the
*output* space. Frames are written to a hidden staging folder and moved into place only after all of them are
written and validated, then `PROJECT_SHOT_TASK_vVVV.conversion_report.json` is written. A failed or cancelled run
removes its partial frames and leaves `...conversion_report.FAILED.json` / `.CANCELLED.json` instead.

## Colour management

* OCIO config: `--ocio-config` > `$OCIO` > OCIO's built-in ACES studio config (`ocio://studio-config-latest`).
* Input colour space `auto` reports **DETECTED**, **INFERRED** (requires `--accept-inferred-colorspace`
  or the GUI checkbox) or **UNKNOWN** (requires a manual choice). Manual choices always win and are recorded.
* Video: YUV->RGB decoding (matrix, range) is kept separate from the OCIO transform so nothing is applied twice.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest      # PowerShell: $env:QT_QPA_PLATFORM="offscreen"; python -m pytest
```
Test media (EXR, MOV/MP4) is generated on the fly; nothing large is committed.
