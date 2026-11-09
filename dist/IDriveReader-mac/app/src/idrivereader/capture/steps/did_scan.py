"""
Step 6 — ReadDataByIdentifier sweep of one ECU (0x0000–0xFFFF by default, both ends inclusive).

Every identifier that exists (positive answer, or a negative answer other than
``requestOutOfRange``) is written to ``didscan/<label>_0x<addr>.jsonl`` as it is found, so an
interrupted scan still leaves everything read so far on disk. Identifiers that got no answer are
retried once at the end with a longer timeout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from ...domain.decoders import ascii_preview
from ...domain.ecus import Ecu
from ...transport.uds import nrc_name
from ...transport.uds_client import UdsResponse
from ..context import CaptureContext, ScanStats, StepAborted, StepOutcome, StepStatus, VehicleFindings
from ..store import JsonLinesWriter
from .common import SilenceWatchdog, try_read_did

_PROGRESS_EVERY = 128


@dataclass
class _ScanTally:
    """Everything one scan run accumulates; written to disk as it grows."""

    writer: JsonLinesWriter
    positives: Dict[int, bytes] = field(default_factory=dict)
    negative: int = 0
    not_supported: int = 0
    silent: List[int] = field(default_factory=list)
    conditional: Set[int] = field(default_factory=set)

    def record(self, did: int, response: UdsResponse) -> None:
        if response.is_positive:
            value = response.payload
            self.positives[did] = value
            self.writer.write({"did": f"{did:04X}", "status": "positive", "hex": value.hex(),
                               "text": ascii_preview(value), "length": len(value)})
        elif response.is_out_of_range:
            self.not_supported += 1
        else:
            self.negative += 1
            if response.is_conditions_not_correct:
                self.conditional.add(did)
            code = response.negative_code
            self.writer.write({"did": f"{did:04X}", "status": "negative",
                               "nrc": f"{code:02X}" if code is not None else None,
                               "reason": nrc_name(code) if code is not None else response.raw.hex()})

    def take_silent(self) -> List[int]:
        silent, self.silent = self.silent, []
        return silent

    def stats(self) -> ScanStats:
        return ScanStats(len(self.positives), self.negative, self.not_supported, len(self.silent))


class DidScanStep:
    def __init__(self, ecu: Ecu, first_did: int = 0x0000, last_did: int = 0xFFFF,
                 timeout: float = 1.0, retry_timeout: float = 3.0):
        self.ecu = ecu
        self.title = f"DID taramasi: {ecu.short_label} (0x{ecu.address:02X})"
        self._first = first_did
        self._last = last_did
        self._timeout = timeout
        self._retry_timeout = retry_timeout

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return (self._last - self._first + 1) * seconds_per_request

    def run(self, context: CaptureContext) -> StepOutcome:
        file_stem = f"didscan/{self.ecu.short_label}_0x{self.ecu.address:02X}"
        watchdog = SilenceWatchdog(context, self.ecu.address)
        tally = _ScanTally(context.store.open_json_lines(f"{file_stem}.jsonl"))
        aborted: Optional[str] = None
        try:
            self._sweep(context, range(self._first, self._last + 1), self._timeout, watchdog, tally)
            retry = tally.take_silent()
            if retry:
                context.progress.log(f"{len(retry)} cevapsiz DID tekrar deneniyor...")
                self._sweep(context, retry, self._retry_timeout, watchdog, tally)
        except StepAborted as error:
            aborted = str(error)
        finally:
            tally.writer.close()
            stats = tally.stats()
            context.findings.did_values[self.ecu.address] = tally.positives
            context.findings.conditional_dids[self.ecu.address] = frozenset(tally.conditional)
            context.findings.scan_stats[self.ecu.address] = stats
            context.store.write_json(f"{file_stem}_summary.json", {
                "target": f"0x{self.ecu.address:02X}", "range": [f"{self._first:04X}", f"{self._last:04X}"],
                "stats": stats, "silent_dids": [f"{d:04X}" for d in tally.silent], "aborted": aborted})

        summary = f"{stats.positive} okunabilir DID, {stats.negative} kilitli/kosullu, {stats.silent} cevapsiz"
        if aborted:
            return StepOutcome(StepStatus.PARTIAL, f"{summary} - {aborted}")
        return StepOutcome(StepStatus.OK, summary)

    def _sweep(self, context: CaptureContext, identifiers: Sequence[int], timeout: float,
               watchdog: SilenceWatchdog, tally: _ScanTally) -> None:
        started = time.monotonic()
        total = len(identifiers)
        for index, did in enumerate(identifiers):
            context.raise_if_cancelled()
            if index % _PROGRESS_EVERY == 0:
                context.progress.step_progress(index / total, _eta_text(index, total, started))
                tally.writer.flush()
            response = try_read_did(context, self.ecu.address, did, timeout)
            if response is None:
                tally.silent.append(did)
                watchdog.record_silence()
                continue
            watchdog.record_answer()
            tally.record(did, response)


def _eta_text(done: int, total: int, started: float) -> str:
    elapsed = time.monotonic() - started
    if done == 0 or elapsed <= 0:
        return f"0/{total}"
    remaining = elapsed / done * (total - done)
    return f"{done}/{total} - kalan ~{int(remaining // 60)} dk {int(remaining % 60)} sn"
