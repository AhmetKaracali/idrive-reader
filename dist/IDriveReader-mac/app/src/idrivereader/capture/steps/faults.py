"""Step 4 — the fault memory (DTCs) of every ECU. Read only: nothing is cleared."""
from __future__ import annotations

from ...domain.decoders import decode_dtc_response
from ...domain.ecus import ecu_label
from ...transport.uds import READ_DTC_BY_STATUS_ALL, nrc_name
from ..context import CaptureContext, FaultReading, StepOutcome, StepStatus, VehicleFindings
from .common import expected_ecu_count, try_request


class FaultMemoryStep:
    title = "Ariza kodlari (DTC)"

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return expected_ecu_count(findings) * seconds_per_request * 3  # DTC answers are longer

    def run(self, context: CaptureContext) -> StepOutcome:
        ecus = context.findings.ecus
        report = {}
        total = 0
        for index, address in enumerate(ecus):
            context.raise_if_cancelled()
            context.progress.step_progress(index / max(len(ecus), 1), ecu_label(address))
            reading, raw = self._read(context, address)
            context.findings.faults[address] = reading
            report[f"0x{address:02X}"] = {"name": ecu_label(address), "reading": reading, "raw": raw}
            total += len(reading.memory.dtcs) if reading.memory else 0
        context.store.write_json("dtcs.json", report)
        context.progress.log(f"Toplam {total} ariza kaydi okundu.")
        return StepOutcome(StepStatus.OK, f"{total} ariza kaydi ({len(ecus)} modul)")

    @staticmethod
    def _read(context: CaptureContext, address: int) -> tuple[FaultReading, str]:
        response = try_request(context, address, READ_DTC_BY_STATUS_ALL, timeout=4.0)
        if response is None:
            return FaultReading.failed("cevap yok"), ""
        if response.negative_code is not None:
            return FaultReading.failed(nrc_name(response.negative_code)), response.raw.hex()
        try:
            return FaultReading.read(decode_dtc_response(response.raw)), response.raw.hex()
        except ValueError as error:
            return FaultReading.failed(str(error)), response.raw.hex()
