# Shot2EXR - developer notes for Claude sessions

Full requirements: `SPEC.md` (the original spec; read the relevant section before changing behaviour).
Work milestone by milestone; do not start the next milestone until the owner asks.

## Purpose
MOV/MP4 or EXR sequence in -> OCIO-converted, resized EXR sequence out, named
`PROJECT_SHOT_TASK_vVVV.FRAME.exr`, plus `PROJECT_SHOT_TASK_vVVV.conversion_report.json`.
Primary platform Rocky Linux 9, also Windows 10/11. Same codebase.

## Architecture (`src/shot2exr/`)
- `config.py` constants/defaults; `errors.py` exception hierarchy + `ExitCode`; `models.py` dataclasses.
- `naming.py` pure naming/frame rules; `validation.py` parameter parsing/validation (all problems at once).
- `media_probe.py` FFprobe (argument lists, never `shell=True`; `parse_ffprobe` is pure).
- `sequence_detector.py` filesystem-only EXR sequence discovery; `exr_reader.py` OIIO header reads.
- `color_manager.py` OCIO config loading (explicit > `$OCIO` > `ocio://studio-config-latest`) and lookups.
- `colorspace_detector.py` DETECTED / INFERRED / UNKNOWN from metadata, keyed by Color Interop IDs.
- `settings.py` TOML settings (bundled `default_settings.toml` < user file / `$SHOT2EXR_SETTINGS`): per-OS
  `projects_root`, `[projects]` and `[tasks]` code -> folder maps. `output_paths.py` is the ONLY output-path
  builder (`resolve_output_location`), used by converter, CLI and GUI preview.
- `resize.py` FIT/FILL/STRETCH geometry. `converter.py` = the engine: `inspect_source`, `plan_conversion`.
- `cli.py` (argparse) and `gui/` (PySide6) are thin layers over `converter`. GUI runs engine calls in
  `QThreadPool` (`gui/workers.py`) and only touches widgets from signal handlers on the GUI thread.

## Decisions
- Deps from conda-forge only (`environment.yml`, python 3.12); `pyproject` lists no runtime deps so
  pip never replaces conda builds (`pip install -e . --no-deps`). `[pip]` extra exists for pip-only setups.
- Video colour: BT.709-tagged video -> INFERRED `Gamma 2.4 Encoded Rec.709` (BT.1886 decode), with
  `Camera Rec.709` named as the alternative. sRGB/linear/gamma2.2 tags -> DETECTED. PQ/HLG -> INFERRED display
  spaces. Untagged/SD -> UNKNOWN. Missing range/matrix tags downgrade DETECTED to INFERRED.
  Decode info (YUV matrix, range) is recorded separately (`ColorDetection.decode`) for the M3 decoder.
- EXR colour: `colorInteropID` -> DETECTED; `acesImageContainerFlag` -> DETECTED ACES2065-1;
  chromaticities only -> INFERRED linear of those primaries; OCIO file rules (non-default) -> INFERRED;
  differing metadata across frames -> UNKNOWN. `oiio:ColorSpace` is ignored (OIIO derives it).
- Sequence: directory with >1 sequence is an error; passing one frame file selects its sequence.
  Missing / corrupt / mismatched frames are `SourceInfo.errors` (inspection succeeds, planning fails).
- Output dir (Amendment 1 in SPEC.md): `{root}/{ProjectFolder}/VFX/{P}_{SEQ}/{P}_{SHOT}/Tasks/{TaskFolder}/ComfyUI/{element}/{P}_{SHOT}_{task}_vNNN`.
  SEQ = first `_` component of shot (string, zeros kept). Element is required and validated like other tokens.
  Linux root ships empty -> ConfigError (exit 8). Root must exist (unmounted storage is an error); deeper
  missing dirs are a warning ("created when conversion starts"). Auto mode: non-empty existing version dir
  is an error unless overwrite. Manual `--output-dir` override keeps the old per-file collision rules.
  Dry run never creates anything. GUI remembers the last element via QSettings.
- M2 must: create the version dir only at conversion start, write into a temp dir/names and rename on
  success (no complete-looking partial output), and put `element` + resolved directory in the report.
- CLI `--version` alone prints the tool version; `--version 001` is the output version (`--version-number` alias).

## Conventions
pathlib everywhere; no `shell=True`; JSON `null` for unknown values; never write during inspect/dry run;
tests generate their own media (OIIO/FFmpeg) and skip when a dependency is missing.

## Commands
```
conda env create -f environment.yml && conda activate shot2exr && python -m pip install -e . --no-deps
shot2exr-gui | python -m shot2exr.gui        shot2exr --help | python -m shot2exr
QT_QPA_PLATFORM=offscreen python -m pytest
```

## Status
- Milestone 1 (foundation + GUI): done 2026-10-01; Amendment 1 (auto output dirs) done same day. 136 tests pass on Linux (Ubuntu 24.04 container) with
  the conda-forge env (py3.12, OIIO 3.1.17, OCIO 2.5.2, PySide6 6.11.2, FFmpeg 9.0.2) and with pip wheels
  (py3.11, OCIO 2.6, FFmpeg 6.1). GUI tests pass on offscreen and xcb (Xvfb). NOT yet run on Rocky Linux 9
  or Windows; `.github/workflows/tests.yml` is prepared but has never run.
- Next: Milestone 2 (EXR read, OCIO processor, resize, EXR write, report.py, GUI progress/cancel).

## Known limitations / notes
- No pixel conversion yet; Start Conversion / Cancel / Open Report are disabled in the GUI.
- FFmpeg >= 9 ignores `-color_primaries/-color_trc` output options for tagging unless frames are tagged
  (`-vf setparams=...`); test fixtures do both. Keep in mind for M3.
- Video frame count: `nb_frames`, else exact packet count (`-count_packets`), else duration estimate (warned).
- Rotation metadata, non-square pixels and interlacing are reported, not corrected.
