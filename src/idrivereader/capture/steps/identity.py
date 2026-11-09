"""Step 1 — who is this car: VIN and the endpoint we reached it on."""
from __future__ import annotations

from ...domain.ecus import DME, GATEWAY, HEAD_UNIT
from ..context import CaptureContext, StepOutcome, StepStatus, VehicleFindings
from .common import try_read_did

DID_VIN = 0xF190


class VehicleIdentityStep:
    title = "Arac kimligi (VIN)"

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return 3 * seconds_per_request

    def run(self, context: CaptureContext) -> StepOutcome:
        findings = context.findings
        vin_by_module = {}
        for ecu in (GATEWAY, HEAD_UNIT, DME):
            context.raise_if_cancelled()
            response = try_read_did(context, ecu.address, DID_VIN, timeout=3.0)
            if response is not None and response.is_positive:
                vin = response.payload.decode("latin1").strip("\x00 ")
                vin_by_module[f"0x{ecu.address:02X}"] = vin
                findings.vin = findings.vin or vin
        findings.vin = findings.vin or findings.endpoint.vin
        context.store.write_json("vehicle.json", {
            "vin": findings.vin, "vin_by_module": vin_by_module, "ident": findings.endpoint.ident,
            "endpoint": findings.endpoint})
        if not findings.vin:
            return StepOutcome(StepStatus.FAILED, "VIN okunamadi - arac cevap vermiyor")
        context.progress.log(f"VIN: {findings.vin}")
        return StepOutcome(StepStatus.OK, f"VIN {findings.vin}")
