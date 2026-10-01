"""GUI entry point (``shot2exr-gui`` / ``python -m shot2exr.gui``)."""

from __future__ import annotations

import os
import sys


def _check_linux_display() -> str | None:
    if not sys.platform.startswith("linux") or os.environ.get("QT_QPA_PLATFORM"):
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ("No display found (DISPLAY/WAYLAND_DISPLAY unset). Run from a desktop session, use "
                "'ssh -X', or set QT_QPA_PLATFORM=offscreen for headless testing.")
    return None


def main(argv: list[str] | None = None) -> int:
    problem = _check_linux_display()
    if problem:
        print(f"shot2exr-gui: {problem}", file=sys.stderr)
        return 1
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(f"shot2exr-gui: PySide6 is not available ({exc}). Activate the 'shot2exr' conda env.", file=sys.stderr)
        return 6

    from shot2exr.gui.main_window import MainWindow
    from shot2exr.gui.styles import STYLESHEET

    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Shot2EXR")
    app.setOrganizationName("Shot2EXR")
    app.setStyle("Fusion")  # identical look on Linux and Windows
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
