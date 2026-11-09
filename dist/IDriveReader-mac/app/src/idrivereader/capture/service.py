"""
One capture, end to end: wires the recorder, the read-only client, the steps and the output folder
together, and always leaves a complete, zipped folder behind — even if a step or the report fails.
"""
from __future__ import annotations

import platform
import re
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .. import __version__
from ..transport.discovery import VehicleEndpoint
from ..transport.hsfz import HsfzConnection
from ..transport.recorder import FRAMES_FILE, FrameRecorder
from ..transport.uds_client import UdsClient
from .context import (CaptureContext, OperatorPrompt, ProgressSink, SessionStatus, StepResult,
                      VehicleFindings)
from .plan import CaptureOptions, build_steps
from .report import build_report
from .session import CaptureSession
from .store import CaptureStore


@dataclass(frozen=True)
class CaptureResult:
    folder: Path
    archive: Optional[Path]  # None when Windows refused to create the zip; the folder is complete
    results: List[StepResult]
    frame_count: int

    @property
    def all_ok(self) -> bool:
        return all(r.outcome.is_ok for r in self.results)


class _LoggingProgress:
    """Forwards progress to the UI and mirrors every message into log.txt."""

    def __init__(self, inner: ProgressSink, log_path: Path):
        self._inner = inner
        self._file = open(log_path, "a", encoding="utf-8")

    def _write(self, text: str) -> None:
        self._file.write(f"{datetime.now():%H:%M:%S} {text}\n")
        self._file.flush()

    def step_started(self, title: str) -> None:
        self._write(f"=== {title}")
        self._inner.step_started(title)

    def step_progress(self, fraction: float, detail: str) -> None:
        self._inner.step_progress(fraction, detail)

    def log(self, message: str) -> None:
        self._write(message)
        self._inner.log(message)

    def step_finished(self, result: StepResult) -> None:
        self._write(f"--- {result.title}: {result.outcome.status} - {result.outcome.summary}")
        self._inner.step_finished(result)

    def session_progress(self, status: SessionStatus) -> None:
        self._inner.session_progress(status)

    def close(self) -> None:
        self._file.close()


class CaptureService:
    def __init__(self, output_root: Path, folder_prefix: str = "kayit"):
        self._output_root = output_root
        self._prefix = folder_prefix

    def run(self, endpoint: VehicleEndpoint, options: CaptureOptions, *, progress: ProgressSink,
            operator: OperatorPrompt, cancel_requested: threading.Event) -> CaptureResult:
        started = datetime.now()
        stamp = f"{started:%Y%m%d_%H%M%S}"
        store = CaptureStore(self._output_root / f"{self._prefix}_{stamp}")
        recorder = FrameRecorder(store.prepare_path(FRAMES_FILE))
        logged = _LoggingProgress(progress, store.prepare_path("log.txt"))
        client = UdsClient(lambda: HsfzConnection(endpoint.ip, endpoint.port, observer=recorder))
        findings = VehicleFindings(endpoint=endpoint)
        context = CaptureContext(client, store, findings, logged, operator, cancel_requested)
        results: List[StepResult] = []
        clock = time.monotonic()
        try:
            results = CaptureSession(build_steps(options), context).run()
        finally:
            client.close()
            recorder.close()
            try:
                self._write_summary_files(store, findings, options, results, started,
                                          time.monotonic() - clock, recorder.frame_count)
            finally:
                logged.close()
        archive = self._finalise_folder(store, findings.vin, stamp, progress)  # may rename store.root
        return CaptureResult(store.root, archive, results, recorder.frame_count)

    def _finalise_folder(self, store: CaptureStore, vin: Optional[str], stamp: str,
                         progress: ProgressSink) -> Optional[Path]:
        """Best effort: add the VIN to the folder name and zip it. The data is already on disk."""
        safe_vin = re.sub(r"[^A-Za-z0-9]", "", vin or "")
        if safe_vin:
            try:
                store.rename_folder(f"{self._prefix}_{safe_vin}_{stamp}")
            except OSError as error:  # antivirus/Explorer holding a handle; keep the timestamp name
                progress.log(f"Klasor adi degistirilemedi ({error}); kayit {store.root} altinda.")
        try:
            return store.archive()
        except OSError as error:
            progress.log(f"Zip olusturulamadi ({error}); klasor eksiksiz: {store.root}")
            return None

    @staticmethod
    def _write_summary_files(store: CaptureStore, findings: VehicleFindings, options: CaptureOptions,
                             results: List[StepResult], started: datetime, seconds: float,
                             frames: int) -> None:
        finished = datetime.now()
        store.write_json("session.json", {
            "app_version": __version__, "started": started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"), "seconds": round(seconds, 1),
            "computer": platform.node(), "windows": platform.platform(),
            "options": {"scan_dme": options.scan_dme, "scan_head_unit": options.scan_head_unit,
                        "scan_gateway": options.scan_gateway, "live_seconds": options.live_seconds,
                        "did_range": [f"{v:04X}" for v in options.did_range]},
            "frames_recorded": frames, "vin": findings.vin,
            "steps": [{"title": r.title, "status": r.outcome.status, "summary": r.outcome.summary}
                      for r in results]})
        try:
            report = build_report(findings, results, started, finished, __version__)
        except Exception:  # the data on disk matters more than the summary; never lose the zip
            report = "RAPOR olusturulamadi. Ham veriler klasorde eksiksiz.\n\n" + traceback.format_exc()
        store.write_text("RAPOR.txt", report)
