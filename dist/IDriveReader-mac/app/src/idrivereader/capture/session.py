"""Runs capture steps in order; one failing step never prevents the next from running."""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from .context import (CaptureCancelled, CaptureContext, CaptureStep, ProgressSink, SessionStatus,
                      StepOutcome, StepResult, StepStatus, VehicleFindings)

ASSUMED_SECONDS_PER_REQUEST = 0.012  # used until the client has measured the real car
_EMIT_INTERVAL = 0.5  # seconds between overall-progress updates within a step


class SessionProgressTracker:
    """
    Turns per-step progress into an overall picture: step N of M, percent done and time left.

    Time left = the unfinished share of the current step's estimate + the estimates of the steps
    still to come, all priced with the seconds-per-request measured on this car so far.
    """

    def __init__(self, steps: Sequence[CaptureStep], findings: VehicleFindings,
                 measured_seconds_per_request: Callable[[], Optional[float]], clock=time.monotonic):
        self._steps = list(steps)
        self._findings = findings
        self._measured = measured_seconds_per_request
        self._clock = clock
        self._started = clock()
        self._index = 0
        self._fraction = 0.0

    def start_step(self, index: int) -> None:
        self._index = index
        self._fraction = 0.0

    def update_step(self, fraction: float) -> None:
        self._fraction = min(max(fraction, 0.0), 1.0)

    def finish(self) -> SessionStatus:
        self._index, self._fraction = len(self._steps) - 1, 1.0
        return self.status()

    def status(self) -> SessionStatus:
        per_request = self._measured() or ASSUMED_SECONDS_PER_REQUEST
        estimates = [step.estimated_seconds(self._findings, per_request) for step in self._steps]
        remaining = estimates[self._index] * (1.0 - self._fraction) + sum(estimates[self._index + 1:])
        elapsed = self._clock() - self._started
        percent = 100.0 * elapsed / (elapsed + remaining) if elapsed + remaining > 0 else 100.0
        return SessionStatus(self._index + 1, len(self._steps), self._steps[self._index].title,
                             round(percent, 1), max(remaining, 0.0))


class _TrackingProgress:
    """A ProgressSink decorator that feeds the tracker and reports the overall status."""

    def __init__(self, inner: ProgressSink, tracker: SessionProgressTracker, clock=time.monotonic):
        self._inner = inner
        self._tracker = tracker
        self._clock = clock
        self._last_emit = 0.0

    def step_started(self, title: str) -> None:
        self._inner.step_started(title)
        self._emit(force=True)

    def step_progress(self, fraction: float, detail: str) -> None:
        self._tracker.update_step(fraction)
        self._inner.step_progress(fraction, detail)
        self._emit(force=False)

    def log(self, message: str) -> None:
        self._inner.log(message)

    def step_finished(self, result: StepResult) -> None:
        self._tracker.update_step(1.0)
        self._inner.step_finished(result)
        self._emit(force=True)

    def session_progress(self, status: SessionStatus) -> None:
        self._inner.session_progress(status)

    def _emit(self, force: bool) -> None:
        now = self._clock()
        if force or now - self._last_emit >= _EMIT_INTERVAL:
            self._last_emit = now
            self._inner.session_progress(self._tracker.status())


class CaptureSession:
    def __init__(self, steps: Sequence[CaptureStep], context: CaptureContext):
        self._steps = list(steps)
        self._context = context
        self._tracker = SessionProgressTracker(self._steps, context.findings,
                                               lambda: context.client.average_request_seconds)

    def run(self) -> List[StepResult]:
        outer = self._context.progress
        progress = _TrackingProgress(outer, self._tracker)
        self._context.progress = progress  # steps report through the tracker for this run
        try:
            results: List[StepResult] = []
            for index, step in enumerate(self._steps):
                self._tracker.start_step(index)
                if self._context.cancel_requested.is_set():
                    results.append(StepResult(step.title, StepOutcome(StepStatus.CANCELLED, "calistirilmadi")))
                    continue
                progress.step_started(step.title)
                result = StepResult(step.title, self._run_one(step))
                results.append(result)
                progress.step_finished(result)
            if self._steps:
                outer.session_progress(self._tracker.finish())
            return results
        finally:
            self._context.progress = outer

    def _run_one(self, step: CaptureStep) -> StepOutcome:
        try:
            return step.run(self._context)
        except CaptureCancelled:
            return StepOutcome(StepStatus.CANCELLED, "operator durdurdu (okunanlar kaydedildi)")
        except ConnectionError as error:
            self._context.progress.log(f"Baglanti hatasi: {error}")
            return StepOutcome(StepStatus.FAILED, f"baglanti hatasi: {error}")
        except Exception as error:  # a defect in one step must not lose the rest of the session
            self._context.progress.log(f"Beklenmeyen hata: {error!r}")
            return StepOutcome(StepStatus.FAILED, f"beklenmeyen hata: {error!r}")
