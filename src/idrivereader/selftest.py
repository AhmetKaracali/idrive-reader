"""
``IDriveReader.exe --self-test``: runs the same demo capture as the window's "Deneme modu" button,
without a window, and exits 0 when every step succeeded. Proves the packaged program works on a
laptop before going to the car. Output goes to ``kayitlar\\deneme_...``.
"""
from __future__ import annotations

import threading

from . import host
from .capture.context import SessionStatus, StepResult
from .launch import capture_demo
from .capture.plan import CaptureOptions


class _SilentProgress:
    def step_started(self, title: str) -> None:
        pass

    def step_progress(self, fraction: float, detail: str) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def step_finished(self, result: StepResult) -> None:
        pass

    def session_progress(self, status: SessionStatus) -> None:
        pass


class _AutoConfirm:
    def confirm(self, title: str, message: str) -> bool:
        return True

    def inform(self, title: str, message: str) -> None:
        pass


def run_self_test() -> int:
    result = capture_demo(host.resolve_output_root(), CaptureOptions(), progress=_SilentProgress(),
                          operator=_AutoConfirm(), cancel_requested=threading.Event())
    return 0 if result.all_ok else 1
