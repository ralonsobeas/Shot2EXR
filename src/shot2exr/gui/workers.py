"""Background execution for the GUI.

Engine calls run in ``QThreadPool`` workers and report back only through Qt signals, so widgets
are touched exclusively on the GUI thread. Milestone 2 adds a conversion worker with progress
and cooperative cancellation on the same pattern.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from shot2exr.errors import Shot2EXRError


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class EngineTask(QRunnable):
    """Run ``fn(*args, **kwargs)`` off the GUI thread."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = TaskSignals()

    def run(self) -> None:  # executed in a pool thread
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Shot2EXRError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:  # noqa: BLE001 - surface unexpected errors in the log instead of crashing
            self.signals.failed.emit("Unexpected error:\n" + traceback.format_exc())
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
