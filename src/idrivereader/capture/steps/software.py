"""Step 3 — software versions (SVK) and the standard identification DIDs F100–F1FF of every ECU."""
from __future__ import annotations

from ...domain.decoders import ascii_preview, decode_svk_entries
from ...domain.ecus import IDENTIFICATION_DIDS, ecu_label
from ...transport.uds import nrc_name
from ..context import CaptureContext, StepOutcome, StepStatus, VehicleFindings
from .common import expected_ecu_count, try_read_did

DID_SVK = 0xF101
# An ECU that ignores this many identification reads in a row is treated as gone for this step
# (unlike the DID scan, we do not stop to ask the operator for every sleepy module).
_GIVE_UP_AFTER_SILENT = 4


class SoftwareVersionsStep:
    title = "Yazilim surumleri (SVK) ve kimlik bilgileri"

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return len(IDENTIFICATION_DIDS) * expected_ecu_count(findings) * seconds_per_request

    def run(self, context: CaptureContext) -> StepOutcome:
        ecus = context.findings.ecus
        report = {}
        incomplete = []
        for index, address in enumerate(ecus):
            context.raise_if_cancelled()
            context.progress.step_progress(index / max(len(ecus), 1), ecu_label(address))
            report[f"0x{address:02X}"], complete = self._read_ecu(context, address)
            if not complete:
                incomplete.append(ecu_label(address))
        context.store.write_json("software.json", report)
        if incomplete:
            context.progress.log("Yarim kalan moduller: " + ", ".join(incomplete))
            return StepOutcome(StepStatus.PARTIAL, f"{len(ecus) - len(incomplete)}/{len(ecus)} modul tam okundu")
        return StepOutcome(StepStatus.OK, f"{len(ecus)} modulun surumleri okundu")

    def _read_ecu(self, context: CaptureContext, address: int) -> tuple[dict, bool]:
        identifiers = {}
        silent_in_a_row = 0
        complete = True
        for did in IDENTIFICATION_DIDS:
            context.raise_if_cancelled()
            response = try_read_did(context, address, did, timeout=1.5)
            if response is None:
                silent_in_a_row += 1
                if silent_in_a_row >= _GIVE_UP_AFTER_SILENT:
                    complete = False
                    break
                continue
            silent_in_a_row = 0
            if response.is_positive:
                identifiers[f"{did:04X}"] = {"hex": response.payload.hex(), "text": ascii_preview(response.payload)}
                if did == DID_SVK:
                    self._store_svk(context, address, response.payload, identifiers)
            elif not response.is_out_of_range and response.negative_code is not None:
                identifiers[f"{did:04X}"] = {"negative": nrc_name(response.negative_code)}
        entry = {"name": ecu_label(address), "identifiers": identifiers,
                 "svk": [e.text for e in context.findings.software.get(address, [])]}
        if not complete:
            entry["note"] = "modul cevap vermeyi birakti"
        return entry, complete

    @staticmethod
    def _store_svk(context: CaptureContext, address: int, value: bytes, identifiers: dict) -> None:
        try:
            context.findings.software[address] = decode_svk_entries(value)
        except ValueError as error:
            identifiers[f"{DID_SVK:04X}"]["decode_error"] = str(error)
