"""
Thread-safe adapters between the capture worker thread and the Tk main loop.

The worker never touches widgets: it posts callables onto a queue that the UI drains every 100 ms.
Prompts block the worker on an Event until the operator has answered in the UI thread.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Protocol

from ..capture.context import SessionStatus, StepResult

UiCall = Callable[[], None]


class ProgressDisplay(Protocol):
    def show_step(self, title: str) -> None: ...

    def show_progress(self, fraction: float, detail: str) -> None: ...

    def append_log(self, message: str) -> None: ...

    def show_session(self, status: SessionStatus) -> None: ...


class UiDispatcher:
    def __init__(self):
        self._calls: "queue.Queue[UiCall]" = queue.Queue()

    def post(self, call: UiCall) -> None:
        self._calls.put(call)

    def run_pending(self) -> None:
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                return
            call()


class DialogPrompt:
    """:class:`~idrivereader.capture.context.OperatorPrompt` backed by Tk message boxes."""

    def __init__(self, dispatcher: UiDispatcher, parent: tk.Misc):
        self._dispatcher = dispatcher
        self._parent = parent

    def confirm(self, title: str, message: str) -> bool:
        return bool(self._show_and_wait(lambda: messagebox.askyesno(title, message, parent=self._parent)))

    def inform(self, title: str, message: str) -> None:
        self._show_and_wait(lambda: messagebox.showinfo(title, message, parent=self._parent))

    def _show_and_wait(self, show: Callable[[], object]) -> object:
        answered = threading.Event()
        result = {}

        def ask() -> None:
            result["value"] = show()
            answered.set()

        self._dispatcher.post(ask)
        answered.wait()
        return result.get("value")


class ProgressView:
    """:class:`~idrivereader.capture.context.ProgressSink` that updates the main window."""

    def __init__(self, dispatcher: UiDispatcher, display: ProgressDisplay):
        self._dispatcher = dispatcher
        self._display = display

    def step_started(self, title: str) -> None:
        self._dispatcher.post(lambda: self._display.show_step(title))

    def step_progress(self, fraction: float, detail: str) -> None:
        self._dispatcher.post(lambda: self._display.show_progress(fraction, detail))

    def log(self, message: str) -> None:
        self._dispatcher.post(lambda: self._display.append_log(message))

    def step_finished(self, result: StepResult) -> None:
        outcome = result.outcome
        self._dispatcher.post(lambda: self._display.append_log(
            f"[{outcome.status}] {result.title}: {outcome.summary}"))

    def session_progress(self, status: SessionStatus) -> None:
        self._dispatcher.post(lambda: self._display.show_session(status))
