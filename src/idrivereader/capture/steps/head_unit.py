"""
Step 5 — the NBT EVO head unit: its own IP, live CAFD, the coding datagroups (as an NCD backup
that is already correctly signed by the car) and the FSC status DID.
"""
from __future__ import annotations

from typing import Dict, Optional

from third_party.nbtevo import srec

from ...domain.decoders import ascii_preview, decode_module_ip
from ...domain.ecus import HEAD_UNIT
from ...transport.uds import nrc_name
from ...transport.uds_client import UdsResponse
from ..context import CaptureContext, HeadUnitInfo, StepOutcome, StepStatus, VehicleFindings
from .common import try_read_did

DID_MODULE_IP = 0x172A
DID_CAF_ID = 0x300F
DID_FSC_STATUS = 0xFDEC
CODING_DATAGROUPS = range(0x3000, 0x3011)  # 0x3000-0x300D coding, 0x300F CAF_ID, 0x3010 signature
_NCD_HEADER = ("raw", "S00600004844521B")
_NCD_TERMINATOR = ("raw", "S70500000000FA")
_NCD_BYTES_PER_RECORD = 0x20  # keeps every S3 record well under the 255-byte count limit


class HeadUnitStep:
    title = "Head unit (NBT EVO): IP, CAFD, coding yedegi, FSC"

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        return (2 + len(CODING_DATAGROUPS)) * seconds_per_request * 2

    def run(self, context: CaptureContext) -> StepOutcome:
        values: Dict[int, bytes] = {}
        did_report: Dict[str, dict] = {}
        wanted = [DID_MODULE_IP, DID_FSC_STATUS, *CODING_DATAGROUPS]
        for index, did in enumerate(wanted):
            context.raise_if_cancelled()
            context.progress.step_progress(index / len(wanted), f"HU 22 {did:04X}")
            response = try_read_did(context, HEAD_UNIT.address, did, timeout=4.0)
            did_report[f"{did:04X}"] = self._describe(response)
            if response is not None and response.is_positive:
                values[did] = response.payload

        if not values:
            context.store.write_json("headunit/hu.json", {"dids": did_report})
            return StepOutcome(StepStatus.FAILED, "Head unit cevap vermedi")

        coding = {dg: values[dg] for dg in CODING_DATAGROUPS if dg in values}
        info = HeadUnitInfo(
            ip=decode_module_ip(values[DID_MODULE_IP]) if DID_MODULE_IP in values else None,
            caf_id=values[DID_CAF_ID].hex() if DID_CAF_ID in values else None,
            coding_datagroups=tuple(coding))
        context.findings.head_unit = info
        if coding:
            context.store.write_text("headunit/hu_coding_backup.ncd", self._to_ncd(coding))
        context.store.write_json("headunit/hu.json", {
            "address": f"0x{HEAD_UNIT.address:02X}", "ip": info.ip, "caf_id": info.caf_id,
            "coding_datagroups": [f"{dg:04X}" for dg in coding], "dids": did_report})

        missing = len(CODING_DATAGROUPS) - len(coding)
        summary = f"{len(coding)} coding datagroup yedeklendi"
        if info.ip:
            summary += f", HU IP {info.ip.ip}"
        if missing:
            summary += f" ({missing} datagroup yok)"
        return StepOutcome(StepStatus.OK if coding else StepStatus.PARTIAL, summary)

    @staticmethod
    def _describe(response: Optional[UdsResponse]) -> dict:
        if response is None:
            return {"error": "cevap yok"}
        if response.is_positive:
            value = response.payload
            return {"hex": value.hex(), "text": ascii_preview(value), "length": len(value)}
        if response.negative_code is not None:
            return {"negative": nrc_name(response.negative_code)}
        return {"unexpected": response.raw.hex()}

    @staticmethod
    def _to_ncd(coding: Dict[int, bytes]) -> str:
        """The S-record layout nbt-evo-toolkit's read_coding.py uses (address = dg << 8 | offset)."""
        memory: srec.Mem = {}
        records: list = [_NCD_HEADER]
        for datagroup, page in coding.items():
            base = datagroup << 8
            for offset, byte in enumerate(page):
                memory[base + offset] = byte
            for start in range(0, len(page), _NCD_BYTES_PER_RECORD):
                length = min(_NCD_BYTES_PER_RECORD, len(page) - start)
                records.append(("data", "3", 4, base + start, length))
        records.append(_NCD_TERMINATOR)
        return srec.emit(memory, records)
