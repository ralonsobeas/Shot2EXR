"""GUI entry point (``shot2exr-gui`` / ``python -m shot2exr.gui`` / the standalone ``Shot2EXR`` app).

``--self-test`` starts the full main window, lets the event loop run briefly, closes it and exits 0,
so installs and standalone bundles can be validated on a real desktop (or under Xvfb) unattended.
"""

from __future__ import annotations

import os
import sys


def _say(text: str) -> None:
    """Print if there is a console: a windowed Windows build has no stdout/stderr."""
    stream = sys.stderr or sys.stdout
    if stream is not None:
        print(text, file=stream, flush=True)


def _check_linux_display() -> str | None:
    if not sys.platform.startswith("linux") or os.environ.get("QT_QPA_PLATFORM"):
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ("No display found (DISPLAY/WAYLAND_DISPLAY unset). Run from a desktop session, use "
                "'ssh -X', or set QT_QPA_PLATFORM=offscreen for headless testing.")
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    self_test = "--self-test" in argv
    argv = [a for a in argv if a != "--self-test"]
    problem = _check_linux_display()
    if problem:
        _say(f"shot2exr-gui: {problem}")
        return 1
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        from shot2exr.diagnostics import missing_library_hint

        hint = missing_library_hint() if getattr(sys, "frozen", False) else "Activate the 'shot2exr' conda env."
        _say(f"shot2exr-gui: Qt cannot start ({exc}). {hint}")
        return 6

    from shot2exr.gui.main_window import MainWindow
    from shot2exr.gui.styles import STYLESHEET

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Shot2EXR")
    app.setOrganizationName("Shot2EXR")
    app.setStyle("Fusion")  # identical look on Linux and Windows
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    if self_test:
        return _run_self_test(app, window)
    return app.exec()


def _run_self_test(app, window) -> int:
    from PySide6.QtCore import QTimer

    QTimer.singleShot(1500, window.close)
    QTimer.singleShot(2000, app.quit)
    code = app.exec()
    ok = code == 0 and window.centralWidget() is not None and window._cfg is not None
    _say(f"shot2exr-gui self-test {'OK' if ok else 'FAILED'}: Qt platform '{app.platformName()}', "
         f"OCIO config {'loaded' if window._cfg is not None else 'NOT loaded'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
