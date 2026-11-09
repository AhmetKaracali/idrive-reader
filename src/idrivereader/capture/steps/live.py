"""
Step 7 — live value recording with the engine running.

Samples, from the target ECU, every short identifier the DID scan found readable plus every one
that answered "conditionsNotCorrect" with the engine off (many measurements only exist while the
engine runs). Well-known measurements (rpm, temperatures, boost, timing, ethanol...) are read on
every round; all the others rotate through in chunks, so the important channels get a high sample
rate while nothing is left out. Every sample is written with its timestamp — and its decoded value
for the well-known ones — to feed the dashboard work and the replay simulator.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ...domain.ecus import IDENTIFICATION_DIDS, Ecu
from ...domain.measurements import MEASUREMENTS_BY_DID
from ..context import (CaptureContext, LiveStats, MeasurementRange, StepAborted, StepOutcome, StepStatus,
                       VehicleFindings)
from ..store import JsonLinesWriter
from .common import SilenceWatchdog, try_read_did

ROTATING_CHUNK = 25  # other identifiers read per round, after the well-known ones


@dataclass
class _LiveTally:
    writer: JsonLinesWriter
    first_values: Dict[int, bytes] = field(default_factory=dict)
    changing: Set[int] = field(default_factory=set)
    decoded: Dict[int, List[float]] = field(default_factory=dict)  # did -> [min, max, count]
    rounds: int = 0

    def record_value(self, did: int, value: bytes) -> None:
        record = {"t": round(time.time(), 3), "did": f"{did:04X}", "hex": value.hex()}
        measurement = MEASUREMENTS_BY_DID.get(did)
        physical = measurement.decode(value) if measurement else None
        if physical is not None:
            record["v"] = round(physical, 4)
            low, high, count = self.decoded.get(did, [physical, physical, 0])
            self.decoded[did] = [min(low, physical), max(high, physical), count + 1]
        self.writer.write(record)
        if self.first_values.setdefault(did, value) != value:
            self.changing.add(did)

    def record_negative(self, did: int, code: Optional[int]) -> None:
        self.writer.write({"t": round(time.time(), 3), "did": f"{did:04X}",
                           "nrc": f"{code:02X}" if code is not None else None})

    def key_ranges(self) -> Tuple[MeasurementRange, ...]:
        ranges = []
        for did, (low, high, count) in sorted(self.decoded.items()):
            measurement = MEASUREMENTS_BY_DID[did]
            ranges.append(MeasurementRange(did, measurement.name, measurement.unit,
                                           round(low, 3), round(high, 3), int(count)))
        return tuple(ranges)


class LiveCaptureStep:
    def __init__(self, ecu: Ecu, duration_seconds: int, max_value_length: int = 64):
        self.ecu = ecu
        self.title = f"Canli kayit: {ecu.short_label} ({duration_seconds} sn)"
        self._duration = duration_seconds
        self._max_length = max_value_length

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return float(self._duration)

    def run(self, context: CaptureContext) -> StepOutcome:
        priority, rotating = self._select(context)
        if not priority and not rotating:
            return StepOutcome(StepStatus.SKIPPED, "Once DID taramasi gerekli (okunabilir DID yok)")
        go = context.operator.confirm(
            "Canli kayit - motoru calistirin",
            "Motoru calistirin (zaten calisiyorsa oyle kalsin). Arac P konumunda, el freni cekili, "
            "acik havada olsun.\nKayit sirasinda araci KILITLEMEYIN (alarm calabilir).\n\n"
            f"{self._duration} saniye boyunca {len(priority) + len(rotating)} deger okunacak "
            f"({len(priority)} tanesi bilinen onemli deger, her turda).\n"
            "Evet'e bastiktan sonra: ilk birkac saniye rolanti, sonra 2-3 kez kisa gaz verin "
            "(once 2500, sonra 4000 devir civari), sonra tekrar rolanti.\n\n"
            "Hazir olunca 'Evet'e basin. 'Hayir' bu adimi atlar.")
        if not go:
            return StepOutcome(StepStatus.SKIPPED, "Operator canli kaydi atladi")

        file_stem = f"live/{self.ecu.short_label}_0x{self.ecu.address:02X}"
        tally = _LiveTally(context.store.open_json_lines(f"{file_stem}.jsonl"))
        aborted: Optional[str] = None
        started = time.monotonic()
        try:
            self._record(context, priority, rotating, tally, started)
        except StepAborted as error:
            aborted = str(error)
        finally:
            tally.writer.close()
            stats = LiveStats(tally.rounds, len(priority) + len(rotating), round(time.monotonic() - started, 1),
                              tuple(sorted(tally.changing)), tally.key_ranges())
            context.findings.live_stats[self.ecu.address] = stats
            context.store.write_json(f"{file_stem}_summary.json", {
                "target": f"0x{self.ecu.address:02X}", "rounds": stats.cycles, "seconds": stats.seconds,
                "priority_dids": [f"{d:04X}" for d in priority],
                "rotating_dids": [f"{d:04X}" for d in rotating],
                "changing_dids": [f"{d:04X}" for d in stats.changing_dids],
                "key_ranges": stats.key_ranges, "aborted": aborted})
        context.operator.inform(
            "Canli kayit bitti",
            "Motoru durdurabilirsiniz. Kontagi acik birakin (frene basmadan START), "
            "kalan adimlar devam edecek.")
        summary = f"{stats.cycles} tur, {len(stats.changing_dids)} degisen deger, {len(stats.key_ranges)} bilinen deger"
        if aborted:
            return StepOutcome(StepStatus.PARTIAL, f"{summary} - {aborted}")
        return StepOutcome(StepStatus.OK, summary)

    def _select(self, context: CaptureContext) -> Tuple[List[int], List[int]]:
        """(well-known identifiers read every round, everything else read in rotation)."""
        readable = context.findings.did_values.get(self.ecu.address, {})
        conditional = context.findings.conditional_dids.get(self.ecu.address, frozenset())
        candidates = {did for did, value in readable.items() if len(value) <= self._max_length}
        candidates |= set(conditional)
        candidates -= set(IDENTIFICATION_DIDS)
        priority = sorted(did for did in candidates if did in MEASUREMENTS_BY_DID)
        rotating = sorted(candidates - set(priority))
        return priority, rotating

    def _record(self, context: CaptureContext, priority: List[int], rotating: List[int],
                tally: _LiveTally, started: float) -> None:
        watchdog = SilenceWatchdog(context, self.ecu.address)
        deadline = started + self._duration
        position = 0
        while time.monotonic() < deadline:
            batch = priority + rotating[position:position + ROTATING_CHUNK]
            position = position + ROTATING_CHUNK if position + ROTATING_CHUNK < len(rotating) else 0
            for did in batch:
                context.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    break
                response = try_read_did(context, self.ecu.address, did, timeout=1.0)
                if response is None:
                    watchdog.record_silence()
                    continue
                watchdog.record_answer()
                if response.is_positive:
                    tally.record_value(did, response.payload)
                else:
                    tally.record_negative(did, response.negative_code)
            tally.rounds += 1
            tally.writer.flush()
            elapsed = time.monotonic() - started
            context.progress.step_progress(
                min(elapsed / self._duration, 1.0),
                f"{tally.rounds}. tur, {len(tally.changing)} degisen deger, "
                f"kalan {max(int(self._duration - elapsed), 0)} sn")
