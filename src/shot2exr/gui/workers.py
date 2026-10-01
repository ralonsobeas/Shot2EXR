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


class ConversionSignals(QObject):
    planned = Signal(object)  # ConversionPlan (emitted whether or not it is ok)
    progress = Signal(int, int, str)  # done, total, filename
    succeeded = Signal(object)  # ConversionResult (any status)
    failed = Signal(str)
    finished = Signal()


class ConversionTask(QRunnable):
    """Plan, then convert frame by frame. Cancellation is cooperative (checked between frames)."""

    def __init__(self, request: Any, cfg: Any, inspection: Any, settings: Any):
        super().__init__()
        import threading

        self.request, self.cfg, self.inspection, self.settings = request, cfg, inspection, settings
        self.cancel_event = threading.Event()
        self.signals = ConversionSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:  # executed in a pool thread
        from shot2exr.converter import plan_conversion, run_conversion

        try:
            plan = plan_conversion(self.request, self.cfg, self.inspection, self.settings)
            self.signals.planned.emit(plan)
            if plan.ok and not self.cancel_event.is_set():
                result = run_conversion(plan, self.cfg, progress=self.signals.progress.emit, cancel=self.cancel_event)
                self.signals.succeeded.emit(result)
        except Shot2EXRError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:  # noqa: BLE001
            self.signals.failed.emit("Unexpected error:\n" + traceback.format_exc())
        finally:
            self.signals.finished.emit()
