# Installing Shot2EXR

Two ways to install:

* **Standalone bundle**: unzip and run. It needs no Python or conda. Use this for artists.
* **Conda environment**: for development, or to run from source.

After installing, run the environment check. It tests every native dependency the way the engine
uses it, and tells you exactly what is missing:

```
shot2exr-cli --check-environment      # standalone bundle
shot2exr --check-environment          # conda install
```

In the GUI, the same check is under **Help > Check environment**.

---

## 1. Standalone bundle

The bundles are built by the `package-*` CI jobs and attached to each run as artifacts:

* `Shot2EXR-<version>-linux-x86_64.tar.gz`
* `Shot2EXR-<version>-windows-x64.zip`

Each archive holds one `Shot2EXR` folder with:

| File | What it is |
|---|---|
| `Shot2EXR` / `Shot2EXR.exe` | The GUI. On Windows it opens no console window. |
| `shot2exr-cli` / `shot2exr-cli.exe` | The command-line tool, with the same options as `shot2exr`. |
| `_internal/` | Python, Qt, OpenImageIO, OpenColorIO, FFmpeg and FFprobe. Don't move files out of it. |
| `BUILD_INFO.txt` | The versions of every bundled dependency, and the OS the bundle was built on. |

FFmpeg and FFprobe are bundled, so a system FFmpeg is never used. You can still point at a
different FFmpeg with `--ffmpeg-path` and `--ffprobe-path`. The ACES studio OCIO config is built
in. To use a studio config instead, set `$OCIO` or pass `--ocio-config`.

### Rocky Linux 9

```bash
tar -xzf Shot2EXR-0.1.0-linux-x86_64.tar.gz -C ~/apps
~/apps/Shot2EXR/shot2exr-cli --check-environment
~/apps/Shot2EXR/Shot2EXR                         # GUI
```

The CLI runs on a minimal Rocky Linux 9 install with no extra packages. The GUI also needs the
desktop graphics libraries from the OS. Qt has to use the system OpenGL driver, so these are
deliberately not bundled. A normal Rocky 9 workstation install ("Server with GUI" or
"Workstation") already has them. On a minimal install, ask an admin to run:

```bash
sudo dnf install libglvnd-opengl libglvnd-egl libglvnd-glx mesa-libGL mesa-libEGL fontconfig \
    libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-renderutil xcb-util-image
```

This is the same list the `package-rocky9` CI job installs on a clean `rockylinux:9` container
before it runs the GUI self-test.

Optional: to put both commands on your PATH, link them from a directory that is already on it:

```bash
mkdir -p ~/.local/bin
ln -s ~/apps/Shot2EXR/Shot2EXR ~/.local/bin/Shot2EXR
ln -s ~/apps/Shot2EXR/shot2exr-cli ~/.local/bin/shot2exr
```

To force X11 under a Wayland session, run `QT_QPA_PLATFORM=xcb Shot2EXR`.

### Windows 10/11

1. Unzip `Shot2EXR-0.1.0-windows-x64.zip` to a local folder, for example
   `C:\Program Files\Shot2EXR` (needs admin) or `%LOCALAPPDATA%\Programs\Shot2EXR`.
2. Double-click `Shot2EXR.exe`. You can pin it to Start or create a shortcut.
3. Optional check, in PowerShell:

```powershell
& "$env:LOCALAPPDATA\Programs\Shot2EXR\shot2exr-cli.exe" --check-environment
```

The bundle is not code-signed. SmartScreen may warn the first time; choose **More info > Run anyway**.
Use a short folder path: project paths under `T:\Volumes\Projects\...` are long, so enable Windows
long paths if a conversion reports "the path is too long".

### Verifying a bundle on a new machine

`packaging/smoke_test.py` from the repository needs only the standard library (Python 3.9+, such as
Rocky's system `python3`). It runs a full distribution test against an unpacked bundle: the
environment check, a ProRes MOV made with the bundled FFmpeg, MOV to EXR, EXR to EXR, error exit
codes and the GUI self-test.

```bash
python3 packaging/smoke_test.py ~/apps/Shot2EXR --gui xcb      # Rocky 9 desktop
```
```powershell
python packaging\smoke_test.py "$env:LOCALAPPDATA\Programs\Shot2EXR"   # Windows
```

---

## 2. Conda environment (development / from source)

All native dependencies (Qt, OpenImageIO, OpenColorIO, FFmpeg) come from **conda-forge**. Use
Miniforge or micromamba; no root access is needed.

### Rocky Linux 9 (bash)

```bash
# If conda isn't installed yet (user-level, no sudo):
curl -LO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p "$HOME/miniforge3" && source "$HOME/miniforge3/bin/activate"

cd Shot2EXR
conda env create -f environment.yml
conda activate shot2exr
python -m pip install -e . --no-deps
shot2exr --check-environment
shot2exr-gui
```

The GUI needs a desktop session (X11 or Wayland). On a minimal or server install, install the same
desktop libraries as for the standalone bundle (see above).

### Windows 10/11 (PowerShell or Miniforge Prompt)

```powershell
cd Shot2EXR
conda env create -f environment.yml
conda activate shot2exr
python -m pip install -e . --no-deps
shot2exr --check-environment
shot2exr-gui
```

`--no-deps` keeps pip from replacing the conda-forge builds.

| | Rocky Linux 9 | Windows |
|---|---|---|
| GUI | `shot2exr-gui` | `shot2exr-gui` (no console window) |
| GUI fallback | `python -m shot2exr.gui` | `python -m shot2exr.gui` |
| CLI | `shot2exr --help` | `shot2exr --help` |
| CLI fallback | `python -m shot2exr --help` | `python -m shot2exr --help` |

### Building the standalone bundle yourself

Build on the platform you are targeting: the Linux bundle on Rocky Linux 9, and the Windows bundle
on Windows. An Ubuntu build is not validated for Rocky.

```bash
conda activate shot2exr
conda install -c conda-forge "pyinstaller>=6.10"
python packaging/build.py --test          # -> dist/Shot2EXR/ and dist/Shot2EXR-<version>-<platform>.*
```

---

## Settings

Projects root and folder mappings are described in the README ("Output directory structure"). The
user settings file is the same for both kinds of install:

* Rocky Linux 9: `~/.config/shot2exr/settings.toml`
* Windows: `%APPDATA%\Shot2EXR\settings.toml`

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not load the Qt platform plugin "xcb"` or `libOpenGL.so.0: cannot open shared object file` | Install the Rocky 9 desktop libraries listed above. `--check-environment` names the missing library. |
| `No display found` | Run from a desktop session, or use `ssh -X`. The CLI works without a display. |
| `FFmpeg ... not found` (conda install) | Activate the env, or pass `--ffmpeg-path` and `--ffprobe-path`. |
| `projects_root ... not reachable` | Mount the project storage, or set `projects_root` in your settings file. |
| Exit code 6 from `--check-environment` | A required dependency is broken. The `FAIL` line says which one and why. |
