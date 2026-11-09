"""Step 2 — which ECUs are on the car (functional TesterPresent + a physical sweep)."""
from __future__ import annotations

from ...domain.ecus import FUNCTIONAL_ADDRESS, ecu_label
from ...transport.uds import TESTER_PRESENT
from ..context import CaptureContext, StepOutcome, StepStatus, VehicleFindings
from .common import try_request

_SWEEP = [a for a in range(0x01, 0xF0) if a != FUNCTIONAL_ADDRESS]


class EcuInventoryStep:
    title = "Modul listesi"

    def __init__(self, functional_window: float = 3.0, sweep_timeout: float = 0.35):
        self._window = functional_window
        self._sweep_timeout = sweep_timeout

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        # Most sweep addresses are empty and cost the short timeout; present ECUs answer fast.
        return self._window + len(_SWEEP) * self._sweep_timeout * 0.85

    def run(self, context: CaptureContext) -> StepOutcome:
        functional = context.client.request_functional(TESTER_PRESENT, window=self._window)
        found = {address: "functional" for address in functional}
        context.progress.log(f"Fonksiyonel sorguya {len(found)} modul cevap verdi.")

        remaining = [a for a in _SWEEP if a not in found]
        for index, address in enumerate(remaining):
            context.raise_if_cancelled()
            context.progress.step_progress(index / len(remaining), f"Adres taraniyor 0x{address:02X}")
            if try_request(context, address, TESTER_PRESENT, timeout=self._sweep_timeout) is not None:
                found[address] = "physical"

        context.findings.ecus = sorted(found)
        context.store.write_json("ecus.json", [
            {"address": f"0x{a:02X}", "name": ecu_label(a), "found_by": found[a]}
            for a in context.findings.ecus])
        for address in context.findings.ecus:
            context.progress.log(f"  {ecu_label(address)}")
        if not found:
            return StepOutcome(StepStatus.FAILED, "Hicbir modul cevap vermedi")
        return StepOutcome(StepStatus.OK, f"{len(found)} modul bulundu")
