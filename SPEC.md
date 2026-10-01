<!-- Original functional specification provided by Rodrigo on 2026-10-01. Keep this file as the source of truth for requirements; record decisions in CLAUDE.md. -->

# PROJECT: Shot2EXR
## Professional Cross-Platform VFX Media Conversion Tool

You are a senior Python software engineer specializing in professional VFX pipelines, image processing, OpenEXR, OpenImageIO, OpenColorIO, FFmpeg and cross-platform desktop application development.

I want you to develop a professional standalone application called **Shot2EXR**.

The purpose of Shot2EXR is to ingest either a video file (MOV/MP4) or an existing OpenEXR image sequence, apply the requested color space and resolution conversions, and export a correctly named EXR sequence ready for use in a professional VFX production pipeline.

The application must include BOTH:

1. A graphical user interface (GUI) for everyday artist usage.
2. A command-line interface (CLI) for automation and pipeline integration.

Both interfaces must communicate with the same underlying conversion engine.

I want a reliable, well-structured application, not a collection of disconnected scripts.

Read this complete specification before making architectural decisions.

**IMPORTANT:** Develop the project incrementally. Understand the entire specification, but only implement Milestone 1 initially. Do not attempt to complete the entire application in one execution.

---

# 1. TARGET PLATFORMS

Shot2EXR must be compatible with:

- **Rocky Linux 9 (PRIMARY production environment).**
- Windows 10/11 (64-bit).

Rocky Linux 9 is our main production operating system and should be treated as the primary Linux compatibility target.

Do not assume that Ubuntu or Debian is the target environment.

The application must use the same Python codebase on both operating systems.

Specific requirements:

- Use Python 3.11+.
- Use pathlib for filesystem operations.
- Prefer Conda-Forge for Python and native dependency management.
- Ensure PySide6, OpenImageIO, OpenColorIO and FFmpeg are compatible with Rocky Linux 9.
- Consider glibc, libstdc++ and Qt platform plugin compatibility.
- Avoid Ubuntu/Debian-specific installation instructions.
- If system-level dependencies are required, provide Rocky Linux / DNF instructions.
- Do not assume that users have root or sudo permissions.
- Keep installation as self-contained as reasonably possible.
- Correctly handle filesystem paths containing spaces and special characters.
- Avoid platform-specific shell commands and shell=True.

For the Linux GUI, consider the requirements of X11, Wayland and the Qt xcb platform plugin.

Do not assume that a package built on Ubuntu will automatically work on Rocky Linux 9.

Provide separate installation instructions for Rocky Linux 9 and Windows.

---

# 2. TECHNOLOGY STACK

Use the following preferred technologies:

- Python 3.11+ as the primary language.
- PySide6 (Qt for Python) for the graphical interface.
- FFmpeg / FFprobe for video decoding and metadata inspection.
- OpenImageIO (OIIO) for image processing and EXR reading/writing.
- OpenColorIO (PyOpenColorIO) for color management.
- NumPy when necessary.
- Typer or argparse for the command-line interface.
- Pytest for automated testing.
- Conda-Forge for environment and native dependency management.

Investigate compatible dependency versions before creating the environment.yml file.

Avoid introducing unnecessary dependencies.

The application architecture must allow future features to be added without rewriting the conversion engine.

---

# 3. SUPPORTED INPUTS

The application must support two different input types.

## A. VIDEO INPUT

Supported extensions:

- .mov
- .mp4

Use FFprobe to extract media metadata and FFmpeg for decoding.

Extract each original decoded video frame in presentation order.

Do not introduce unintended frame duplication, frame dropping or frame-rate conversion.

Audio is not required and can be ignored.

If the video uses variable frame rate, preserve the actual decoded frames and record relevant source timing information in the conversion report.

## B. EXR SEQUENCE INPUT

The user can select a directory containing an existing numbered EXR sequence.

For example:

source/
    plate.1001.exr
    plate.1002.exr
    plate.1003.exr
    plate.1004.exr

The application must:

- Detect the EXR sequence automatically.
- Sort the frames numerically.
- Identify the original frame range.
- Detect missing frames.
- Calculate the frame count.
- Ignore unrelated files.
- Check sequence consistency.
- Warn if multiple independent EXR sequences are found in the same directory.

Never modify the original input files.

If frames are missing, report the gaps clearly. For the initial implementation, stop and require the user to resolve the issue rather than silently creating replacement frames.

---

# 4. USER PARAMETERS

Shot2EXR must accept the following parameters:

| Parameter | Example | Description |
|---|---|---|
| input_path | /source/clip.mov | Source video or EXR directory |
| project | GOD | Project name |
| shot | 0046_005 | Shot identifier |
| task | ml | Task identifier |
| version | 001 | Output version |
| start_frame | 1009 | First output frame number |
| output_resolution | 2048x1152 | Target width and height |
| input_colorspace | auto | Source color space |
| output_colorspace | ACEScg | Target color space |
| output_directory | /output | Destination directory |

Additional optional parameters:

- ocio_config
- resize_mode
- overwrite
- dry_run
- accept_inferred_colorspace
- ffmpeg_path
- ffprobe_path

Default values:

input_colorspace = auto

resize_mode = fit

overwrite = false

dry_run = false

accept_inferred_colorspace = false

The user must always be able to override automatically detected values when appropriate.

---

# 5. OUTPUT NAMING CONVENTION

All exported EXR files must follow this exact naming convention:

PROJECT_SHOT_TASK_vVERSION.FRAME.exr

Example input parameters:

Project: GOD

Shot: 0046_005

Task: ml

Version: 001

Start Frame: 1009

Expected output:

GOD_0046_005_ml_v001.1009.exr

GOD_0046_005_ml_v001.1010.exr

GOD_0046_005_ml_v001.1011.exr

GOD_0046_005_ml_v001.1012.exr

Continue sequentially until the last source frame has been processed.

Naming requirements:

- Version numbers must use a minimum of three digits.
- Always include the lowercase "v" prefix.
- Accept both "001" and "v001" as version inputs and normalize them internally.
- Frame numbers must use a minimum of four digits.
- Do not truncate frame numbers longer than four digits.
- Output numbering must start at the user-specified start_frame.
- Original source frame numbering must not override the requested output numbering.
- Validate naming parameters to prevent invalid filenames or path traversal.

The GUI must display a live preview of the resulting output filename before conversion begins.

---

# 6. AUTOMATIC INPUT COLOR SPACE DETECTION

This is an important feature.

The application must attempt to detect the original input color space automatically.

The default input color space parameter must be:

auto

However, color space detection must be technically responsible. Do not silently guess when the available metadata is insufficient.

Create a dedicated color space detection module.

## 6.1 VIDEO COLOR SPACE DETECTION

For MOV and MP4 inputs, use FFprobe to inspect relevant metadata, including:

- color_primaries
- color_transfer
- color_space (matrix coefficients)
- color_range
- Additional relevant video stream or container metadata.

Interpret these properties together.

Pay special attention to:

- Rec.709.
- Gamma characteristics.
- Full vs Limited range.
- PQ and HLG transfer functions.
- YUV to RGB decoding.
- Potentially missing or incorrect metadata.

Do not assume that BT.709 primaries alone uniquely identify the full source encoding.

Distinguish between the decoding process (including YUV matrix and range interpretation) and the subsequent OCIO color transformation.

Avoid accidental double conversions.

## 6.2 EXR COLOR SPACE DETECTION

When the input is an EXR sequence, inspect its header metadata using OpenImageIO.

Look for:

- colorInteropID, when available.
- chromaticities.
- acesImageContainerFlag.
- Other relevant color metadata.

Check consistency across the input sequence.

Also support OpenColorIO File Rules as an additional inference mechanism.

Do not automatically assume that every EXR is ACEScg or linear sRGB.

Chromaticities alone do not always provide enough information to identify the complete pixel encoding.

## 6.3 DETECTION RESULTS

The detector must return one of three possible states:

DETECTED:
Sufficient and consistent metadata is available to identify the encoding and map it to the active OCIO configuration.

INFERRED:
A plausible color space has been identified, but some uncertainty remains.

UNKNOWN:
There is insufficient reliable information to identify the input color space.

The application should display:

- Detection state.
- Proposed input color space, if available.
- Relevant source metadata.
- A brief explanation of the detection result.

The user must always be able to manually override the detected color space.

If the input color space is UNKNOWN, require manual selection.

If it is INFERRED, require explicit confirmation before processing.

In non-interactive CLI mode, return a clear actionable error when confirmation is required.

Do not implement visual color space recognition based on pixel appearance in this initial version.

---

# 7. COLOR MANAGEMENT

Use OpenColorIO for color management.

Requirements:

- Support external OCIO configuration files.
- Support the OCIO environment variable.
- Allow the user to select an input color space manually.
- Populate available output color spaces from the active OCIO configuration.
- Validate color space names before starting the conversion.
- Avoid unnecessary transforms when input and output spaces are identical.
- Do not silently substitute unsupported color spaces.

Use a technically correct processing order:

1. Decode and correctly interpret the source pixels.
2. Resolve the input color space.
3. Convert to an appropriate linear working space when necessary.
4. Apply resolution conversion.
5. Transform to the requested output color space.
6. Export the final EXR.

Pay particular attention to:

- Floating-point precision.
- Preservation of valid negative and over-range values.
- Highlight preservation.
- Alpha channels.
- Premultiplied vs straight alpha.
- Avoiding unnecessary clipping.
- Avoiding accidental gamma or display transforms.

Support ACES workflows but do not hardcode the application exclusively to ACES.

The output EXRs must contain pixel values corresponding to the selected output color space, not merely metadata labels indicating that color space.

---

# 8. RESOLUTION CONVERSION

The user must be able to enter a custom output resolution.

Example:

2048x1152

Implement three resizing modes:

FIT:
Preserve the original aspect ratio, fitting the complete image within the requested resolution and adding padding when necessary.

FILL:
Preserve aspect ratio while filling the entire requested resolution, cropping when necessary.

STRETCH:
Force the requested width and height, even if this changes the original aspect ratio.

The default mode should be FIT.

Use a high-quality resizing filter.

Handle alpha appropriately during resizing.

Every exported frame must have exactly the requested output dimensions.

---

# 9. EXR OUTPUT SPECIFICATIONS

Use OpenImageIO to write the final EXR files.

Default export settings:

- Format: OpenEXR.
- Bit depth: HALF FLOAT (16-bit floating point).
- Compression: ZIP.
- Channels: RGB.
- Preserve alpha when available.
- Consistent channel naming and ordering.

Write appropriate output color metadata when technically supported.

Do not blindly copy the original input color metadata after a color transformation.

Never apply an undocumented display transform to the output EXRs.

---

# 10. CONVERSION REPORT

Every successful conversion must generate an additional JSON report.

Naming convention:

PROJECT_SHOT_TASK_vVERSION.conversion_report.json

Example:

GOD_0046_005_ml_v001.conversion_report.json

Save this file in the same output directory as the generated EXR sequence.

The purpose of this report is to provide traceability and allow the conversion settings to be audited later.

The report must contain at least the following information.

## GENERAL INFORMATION

- Tool name.
- Tool version.
- Conversion date and timestamp (ISO 8601).
- Operating system.
- Processing duration.
- Conversion status.
- Relevant dependency versions when available.

## INPUT INFORMATION

- Original source path.
- Source type.
- Original resolution.
- Original frame count.
- Original frame range, when applicable.
- Original FPS and time base, when applicable.
- Original color metadata.
- Missing frames, if any.

## COLOR MANAGEMENT

- Detected input color space.
- Detection state.
- Detection evidence.
- Whether the user manually overrode the detected value.
- Actual input color space used.
- Output color space.
- OCIO configuration path.
- OCIO configuration identity or hash when feasible.
- Color transformations actually performed.

## OUTPUT INFORMATION

- Output directory.
- Output filename pattern.
- Start frame.
- End frame.
- Number of exported frames.
- Output resolution.
- Resize mode.
- EXR bit depth.
- Compression.
- Alpha channel presence.

## VALIDATION

- Expected frame count.
- Actual exported frame count.
- Errors or warnings.
- Final validation result.

Use JSON null when information is unavailable rather than inventing values.

Generate the completed report after validating the export.

If a conversion fails, write a clearly differentiated diagnostic report when feasible, without falsely presenting an incomplete sequence as successful.

The GUI must include an option to open the generated conversion report.

---

# 11. GRAPHICAL USER INTERFACE (GUI)

The application must include a desktop GUI developed using PySide6.

I want a clean, professional, dark interface inspired by modern VFX pipeline utilities.

Prioritize clarity, usability and functionality.

Avoid unnecessary visual effects or animations.

The interface must be responsive and work correctly on Rocky Linux 9 and Windows.

Organize the main window into the following sections.

## A. SOURCE

- Input path field.
- Browse File button.
- Browse Directory button.
- Drag-and-drop support for videos and EXR directories.
- Automatically detected input type.
- Original resolution.
- Source frame count.
- Original frame range, when applicable.
- Automatically detected input color space.
- Detection status.

Include an Inspect Source action that reads the input metadata without starting a conversion.

## B. SHOT INFORMATION

Editable fields:

- Project.
- Shot.
- Task.
- Version.
- Start Frame.

These values must update the output filename preview automatically.

## C. COLOR MANAGEMENT

- Input Color Space dropdown.
- AUTO detection option selected by default.
- Output Color Space dropdown.
- OCIO configuration file selector.
- Input color space detection status.
- Option to inspect original color metadata.
- Warning messages for ambiguous detections.

Populate the available color spaces dynamically from the selected OCIO configuration.

## D. OUTPUT SETTINGS

- Output directory selector.
- Width.
- Height.
- Resize mode: Fit / Fill / Stretch.
- Live output filename preview.
- Expected output frame range.

## E. CONVERSION

Include:

- Inspect / Dry Run button.
- Start Conversion button.
- Cancel Conversion button.
- Progress bar.
- Current processed frame / total frames.
- Processing log.
- Conversion result.
- Open Output Folder button.
- Open Conversion Report button.

Disable or clearly indicate features that are not implemented yet during incremental development.

## F. RESPONSIVENESS

The GUI must never freeze during long processing operations.

Use an appropriate Qt worker/thread architecture.

Communicate progress through Qt signals and slots.

Never manipulate GUI widgets directly from background processing threads.

Implement cooperative cancellation.

Ensure incomplete output is handled safely if processing is interrupted.

The GUI and CLI must use exactly the same conversion engine and validation logic.

Do not duplicate media conversion logic inside the GUI.

---

# 12. COMMAND LINE INTERFACE (CLI)

Shot2EXR must also provide a fully functional command-line interface.

Example:

shot2exr 
  --input "/source/clip.mov" 
  --project GOD 
  --shot 0046_005 
  --task ml 
  --version 001 
  --start-frame 1009 
  --resolution 2048x1152 
  --input-colorspace auto 
  --output-colorspace ACEScg 
  --output-dir "/output"

Support relevant additional options:

- --ocio-config
- --resize-mode
- --overwrite
- --dry-run
- --accept-inferred-colorspace
- --ffmpeg-path
- --ffprobe-path
- --help
- --version

A Dry Run must display:

- Input information.
- Detected source type.
- Source resolution.
- Source frame count.
- Color space detection results.
- Target color space.
- Target resolution.
- Expected output filenames.
- Expected output frame range.
- Output directory.
- Relevant warnings.

A Dry Run must never write EXR files.

Provide understandable error messages and appropriate exit codes.

---

# 13. APPLICATION LAUNCHING

I want the application to be easy to launch on both Rocky Linux 9 and Windows.

Configure two separate entry points using pyproject.toml.

GUI:

shot2exr-gui

CLI:

shot2exr

Use project.gui-scripts and project.scripts as appropriate.

The GUI should not open an unnecessary terminal window on Windows.

Also provide a Python module-based fallback launch method.

During development, the installation workflow should be approximately:

conda env create -f environment.yml

conda activate shot2exr

python -m pip install -e .

The user can then launch the GUI using:

shot2exr-gui

Or access the CLI using:

shot2exr --help

Document equivalent instructions for Windows PowerShell and Rocky Linux 9 terminals.

---

# 14. FUTURE STANDALONE DISTRIBUTION

The application should eventually be distributable as a standalone desktop tool.

Desired result:

Windows:
Shot2EXR.exe

Rocky Linux 9:
Shot2EXR executable or suitable application bundle.

The final distributed GUI should not require the end user to manually install Python or Conda.

Prepare the architecture for packaging with PyInstaller or another appropriate solution.

Consider:

- Bundling native dependencies.
- Qt platform plugins.
- FFmpeg and FFprobe.
- OpenImageIO.
- OpenColorIO.
- OCIO configuration management.
- Rocky Linux 9 system library compatibility.
- Appropriate Linux runtime linking and rpath handling.
- Avoiding unnecessary reliance on system-wide Python packages.

Build and validate Windows and Linux distributions separately.

The Linux distribution must be built and tested specifically against Rocky Linux 9.

Do not rely on an Ubuntu-built binary without validating its compatibility.

IMPORTANT: Standalone packaging is a later milestone. Do not spend development time on it during Milestone 1.

---

# 15. ERROR HANDLING

Shot2EXR must detect and report:

- Invalid input paths.
- Unsupported input extensions.
- Missing source files.
- Unreadable video files.
- Corrupt EXR files.
- Missing EXR sequence frames.
- Multiple sequences in the same directory.
- Missing FFmpeg / FFprobe executables.
- Invalid resolutions.
- Unknown color spaces.
- Invalid OCIO configurations.
- Missing output directories.
- Output permission errors.
- Existing output files.
- Processing interruptions.

Never overwrite existing output files unless the user explicitly enables overwrite.

Avoid leaving incomplete sequences that appear successful.

Prefer predictable memory consumption.

Do not load an entire long EXR sequence into memory.

Use frame-by-frame processing or another memory-efficient strategy.

---

# 16. PROJECT ARCHITECTURE

Create a modular Python project.

Suggested organization:

Shot2EXR/
    README.md
    CLAUDE.md
    SPEC.md
    environment.yml
    pyproject.toml

    src/
        shot2exr/
            __init__.py
            config.py
            models.py
            naming.py
            media_probe.py
            sequence_detector.py
            colorspace_detector.py
            color_manager.py
            video_reader.py
            exr_reader.py
            resize.py
            exr_writer.py
            report.py
            converter.py
            cli.py

            gui/
                __init__.py
                main.py
                main_window.py
                workers.py
                styles.py

    tests/
        test_naming.py
        test_frame_numbers.py
        test_validation.py
        test_media_probe.py
        test_sequence_detection.py
        test_colorspace_detection.py
        test_report.py
        test_conversion.py

    .github/
        workflows/
            tests.yml

You may adjust this structure if you have a technically justified reason.

Do not create unnecessary placeholder modules without functionality.

Keep the application modular, understandable and maintainable.

The GUI and CLI should be thin layers over the same shared conversion engine.

---

# 17. TESTING

Use Pytest.

Plan for automated testing on:

- Windows.
- Rocky Linux 9.

Prefer using a Rocky Linux 9 container or an equivalent dedicated runner for the Linux CI environment.

Do not treat ubuntu-latest as sufficient proof of Rocky Linux 9 compatibility.

For GUI testing, consider an appropriate headless Qt testing setup, such as Xvfb where necessary.

Also validate the packaged GUI on a real Rocky Linux 9 desktop environment before considering the application production-ready.

Tests should eventually cover:

- Output naming conventions.
- Version normalization.
- Frame numbering.
- Input validation.
- EXR sequence discovery.
- Missing-frame detection.
- Video metadata parsing.
- Automatic color space detection.
- Manual color space overrides.
- JSON report generation.
- Resolution handling.
- Output collision detection.
- Dry-run functionality.
- End-to-end conversions using small synthetic test media.
- GUI launch smoke tests.
- Native dependency availability.

Clearly distinguish between tests that were actually executed and those that are only planned.

Do not claim Windows or Rocky Linux compatibility has been verified unless the relevant tests have actually passed.

---

# 18. DEVELOPMENT STRATEGY AND TOKEN EFFICIENCY

IMPORTANT:

I want to develop Shot2EXR incrementally, with careful attention to efficient token usage.

Do not attempt to write the entire application in a single response.

Do not repeatedly rewrite working code.

Avoid excessively long explanations and unnecessary architectural complexity.

Before implementing changes, inspect the existing project files.

Maintain a concise CLAUDE.md containing:

- Project purpose.
- Architecture.
- Technology decisions.
- Development conventions.
- Installation commands.
- Launch commands.
- Current implementation status.
- Known limitations.

Maintain SPEC.md with the complete functional requirements.

These files should allow development to continue across multiple Claude Code sessions without requiring the entire original prompt to be repeated.

Divide development into the following milestones.

## MILESTONE 1 — FOUNDATION + BASIC GUI

Implement:

1. Project folder structure.
2. Reproducible environment.yml compatible with Rocky Linux 9 and Windows.
3. pyproject.toml.
4. Shared application configuration and data models.
5. Input parameter validation.
6. Media metadata inspection.
7. EXR sequence discovery.
8. Basic color space metadata inspection and detection model.
9. Output naming and frame-numbering functions.
10. Functional CLI foundation.
11. A real, launchable PySide6 graphical interface.
12. GUI input selection and parameter fields.
13. Live output filename preview.
14. Working source inspection and Dry Run.
15. Basic automated tests.
16. Initial README, CLAUDE.md and SPEC.md.

The graphical application must actually launch during Milestone 1.

Full media conversion is NOT required yet.

## MILESTONE 2 — EXR CONVERSION ENGINE

Implement:

- EXR reading.
- OCIO color transformations.
- Resolution conversion.
- EXR writing.
- Correct output frame numbering.
- GUI conversion progress.
- Conversion report generation.
- EXR-to-EXR integration tests.

## MILESTONE 3 — VIDEO CONVERSION ENGINE

Implement:

- MOV/MP4 decoding.
- FFprobe metadata integration.
- Video color interpretation.
- Video-to-EXR conversion.
- GUI progress and cancellation.
- Frame-count validation.
- Relevant integration tests.

## MILESTONE 4 — HARDENING AND DISTRIBUTION

Implement:

- Improved error handling.
- Performance and memory improvements.
- Windows and Rocky Linux 9 automated compatibility tests.
- End-to-end validation.
- GUI startup validation on Rocky Linux 9.
- Native dependency compatibility checks.
- Installation documentation.
- Standalone GUI packaging.
- Distribution testing.

---

# 19. INSTRUCTIONS FOR YOUR FIRST EXECUTION

Start working on MILESTONE 1 now.

Do not just provide a conceptual proposal. Create the actual project files in the current working directory.

First, briefly explain your proposed architecture and key technical decisions.

Then implement the foundation.

Set up the development environment and the GUI/CLI entry points.

The GUI must be launchable at the end of Milestone 1 and should already allow the user to enter project information, inspect source media, configure output settings and preview the expected output filenames.

The CLI must support source inspection and Dry Run.

Write the corresponding unit tests.

Execute all tests that can run in the available environment and fix any detected issues.

At the end, provide a SHORT development summary covering:

1. Files created or modified.
2. Implemented functionality.
3. Tests executed and their actual results.
4. How to launch the GUI on Rocky Linux 9.
5. How to launch the GUI on Windows.
6. How to use the CLI.
7. Known limitations.
8. What remains for Milestone 2.

Do not proceed to Milestone 2 until I explicitly ask you to continue.

If a technical decision is ambiguous, choose a sensible, documented default unless it creates a risk to color accuracy, source integrity or output correctness.

Prioritize correctness, maintainability, accurate color management, Rocky Linux 9 compatibility, Windows compatibility and reasonable token usage.

Let's build Shot2EXR.


---

# AMENDMENT 1 (2026-10-01): AUTOMATIC OUTPUT DIRECTORY STRUCTURE

<!-- Provided by Rodrigo; supersedes the 'output_directory' requirements above where they conflict. Implementation decisions: folder names use the underscore naming convention (sequence folder GOD_0046, shot folder GOD_0046_005, version folder GOD_0046_005_ml_v001); see CLAUDE.md. -->

Shot2EXR — Milestone 1 Modification: Automatic Output Directory Structure
Before proceeding with Milestone 2, I want to introduce an important change to Shot2EXR's output directory management.
Instead of requiring the user to manually specify the complete output path for every conversion, the application should automatically construct a standardized VFX production directory using the project, shot, task, element and version information.
This modification must be incorporated into the existing architecture, GUI, CLI, tests and documentation.
Please inspect the current implementation first. Preserve working functionality and avoid unnecessary refactoring.
1. Required Output Structure
This is an example of the exact directory structure I want:
T:/Volumes/Projects/GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water/GOD_0046_005_ml_v001/
The exported EXR files and conversion report must be saved INSIDE this final version directory.
For example:
GOD_0046_005_ml_v001/
GOD_0046_005_ml_v001.1009.exr
GOD_0046_005_ml_v001.1010.exr
GOD_0046_005_ml_v001.1011.exr
GOD_0046_005_ml_v001.conversion_report.json
2. Path Construction Logic
The output path should be generated using the following template:
{PROJECTS_ROOT}/
{PROJECT_FOLDER}/
VFX/
{PROJECT_CODE}{SEQUENCE}/
{PROJECT_CODE}{SHOT}/
Tasks/
{TASK_FOLDER}/
ComfyUI/
{ELEMENT}/
{PROJECT_CODE}{SHOT}{TASK_CODE}_v{VERSION}/
For this example:
PROJECT_CODE = GOD
PROJECT_FOLDER = GodOfTides
SHOT = 0046_005
SEQUENCE = 0046
TASK_CODE = ml
TASK_FOLDER = MachineLearning
ELEMENT = water
VERSION = 001
The sequence identifier must be derived from the first component of the shot identifier.
Example:
0046_005 → 0046
Preserve leading zeros. Do not convert these identifiers into integers unnecessarily.
The folder names VFX, Tasks and ComfyUI are fixed parts of the directory template.
The final version directory must be generated automatically using the established Shot2EXR naming convention.
3. Project and Task Folder Mapping
Please distinguish between short project/task identifiers and their corresponding filesystem directory names.
For example:
Project code:
GOD
Project folder:
GodOfTides
Task code:
ml
Task folder:
MachineLearning
Do not assume that the long project name can always be inferred from its three-letter code.
Implement a simple configurable mapping mechanism so that more projects and task types can be added later without modifying the source code.
For now, provide GOD → GodOfTides and ml → MachineLearning as the initial mappings.
A TOML configuration file would be appropriate.
4. New GUI Field: Element / Subtask
Add a new editable field to the GUI called:
Element / Subtask
Example value:
water
This field corresponds to the directory immediately preceding the final version directory.
Users must be able to specify different elements, such as:

* water
* fire
* smoke
* environment
* etc.

Do not confuse this field with the existing Task identifier (ml).
Make this field required and validate it to prevent invalid directory names, path separators or path traversal.
The application should remember the last used element when practical, but it must always remain editable.
5. Cross-Platform Root Directory Configuration
This feature must work correctly on both:

* Rocky Linux 9 (primary production environment).
* Windows 10/11.

The shared storage may be mounted at different filesystem locations depending on the operating system.
For Windows, the current example root is:
T:/Volumes/Projects
For Rocky Linux 9, the root must be configurable according to the actual production storage mount point.
Do not invent or hardcode a Linux mount location.
Implement platform-specific root configuration.
For example, the application configuration could contain:
[paths.windows]
projects_root = "T:/Volumes/Projects"
[paths.linux]
projects_root = ""
An empty Linux root should produce a clear configuration message rather than silently writing to an unintended directory.
Allow the root directory to be configured through the application settings and optionally overridden via CLI.
Use pathlib.Path to construct paths natively on the current operating system.
Do not simply replace forward slashes with backslashes in a Windows path.
Do not duplicate path-building logic in the GUI and CLI.
6. Automatic Output Path Preview
Modify the GUI so the final output directory is automatically generated as the user edits:

* Project.
* Shot.
* Task.
* Element.
* Version.

Display the complete resolved output path in a read-only preview field.
Also display the expected first EXR filename.
The user should not normally need to type the complete output directory manually.
However, retain an optional advanced manual output-directory override for exceptional cases.
The default behavior must be automatic directory generation.
7. CLI Changes
Add support for the new parameter:
--element water
Optionally support:
--projects-root "/custom/projects/root"
Keep the existing --output-dir parameter as an explicit advanced override, but make it optional when automatic path generation is configured.
For example:
shot2exr 
--input "/source/clip.mov" 
--project GOD 
--shot 0046_005 
--task ml 
--element water 
--version 001 
--start-frame 1009 
--resolution 2048x1152 
--input-colorspace auto 
--output-colorspace ACEScg 
--dry-run
The application must resolve the complete destination path automatically.
8. Directory Creation and Safety
The required directory structure should be created automatically when starting a real conversion.
However:

* Dry Run must never create directories.
* Source files must remain untouched.
* Validate the resolved output path before creating it.
* Check existing version directories and potential filename collisions.
* Never silently overwrite an existing EXR sequence.
* Preserve the existing overwrite protection.
* Do not create partially populated output directories that appear to represent completed conversions.

The conversion_report.json file must be written inside the automatically generated version folder.
Also include the resolved output path and selected element in the JSON report.
9. Testing
Add automated tests covering:

* Automatic output path construction.
* Project code to project folder mapping.
* Task code to task folder mapping.
* Sequence extraction from shot identifiers.
* Preservation of leading zeros.
* Element name validation.
* Version directory generation.
* Windows-style path construction.
* Rocky Linux path construction.
* Automatic output directory preview.
* Explicit output directory overrides.
* Dry Run not creating directories.
* Existing output collision handling.

10. Development Instructions
Treat this as an extension of Milestone 1.
Do not start Milestone 2 yet.
Please:

1. Inspect the existing project.
2. Update the shared configuration and output path builder.
3. Update the GUI with the new Element / Subtask field.
4. Implement the automatic output directory preview.
5. Update the CLI.
6. Update the tests.
7. Update CLAUDE.md, SPEC.md and README.md.
8. Run the available tests and fix any regressions.

Keep the directory generation logic centralized and shared between the GUI and CLI.
At the end, briefly summarize the modifications and provide an example of the resulting output path for both Windows and Rocky Linux 9.
Do not proceed to Milestone 2 until I explicitly request it.
