"""Builds RAPOR.txt — the human-readable summary of a capture session."""
from __future__ import annotations

from datetime import datetime
from typing import List, Sequence

from ..domain.ecus import ecu_label
from .context import StepResult, StepStatus, VehicleFindings

_STATUS = {StepStatus.OK: "TAMAM", StepStatus.PARTIAL: "KISMEN", StepStatus.SKIPPED: "ATLANDI",
           StepStatus.FAILED: "HATA", StepStatus.CANCELLED: "DURDURULDU"}
_TIME = "%Y-%m-%d %H:%M:%S"


def build_report(findings: VehicleFindings, results: Sequence[StepResult],
                 started: datetime, finished: datetime, app_version: str) -> str:
    endpoint = findings.endpoint
    lines = [f"IDriveReader {app_version} - okuma raporu", "=" * 60,
             f"Baslangic : {started.strftime(_TIME)}",
             f"Bitis     : {finished.strftime(_TIME)}",
             f"VIN       : {findings.vin or '-'}",
             f"Baglanti  : {endpoint.ip}:{endpoint.port} ({endpoint.found_by})",
             "", "ADIMLAR", "-" * 60]
    lines += [f"[{_STATUS[r.outcome.status]:>10}] {r.title}: {r.outcome.summary}" for r in results]
    lines += _software_section(findings) + _fault_section(findings) + _head_unit_section(findings)
    lines += _scan_section(findings) + _live_section(findings)
    lines += ["", "Bu oturumda araca HICBIR yazma, silme veya kodlama komutu gonderilmedi.",
              "Tum ham trafik: frames.jsonl"]
    return "\n".join(lines) + "\n"


def _software_section(findings: VehicleFindings) -> List[str]:
    lines = ["", "MODULLER VE YAZILIM SURUMLERI", "-" * 60]
    for address in findings.ecus:
        lines.append(ecu_label(address))
        lines += [f"      {entry.text}" for entry in findings.software.get(address, [])]
    return lines


def _fault_section(findings: VehicleFindings) -> List[str]:
    lines = ["", "ARIZA KODLARI", "-" * 60]
    for address, reading in sorted(findings.faults.items()):
        if reading.error:
            lines.append(f"{ecu_label(address)}: okunamadi ({reading.error})")
        elif reading.memory and reading.memory.dtcs:
            codes = ", ".join(f"{d.code}{'*' if d.is_active else ''}" for d in reading.memory.dtcs)
            lines.append(f"{ecu_label(address)}: {len(reading.memory.dtcs)} kayit -> {codes}")
    lines.append("(* = su an aktif. Aciklamalar icin kodlar ISTA/E-Sys ile eslestirilmeli.)")
    return lines


def _head_unit_section(findings: VehicleFindings) -> List[str]:
    info = findings.head_unit
    lines = ["", "HEAD UNIT", "-" * 60]
    if info is None:
        return lines + ["Head unit okunamadi."]
    return lines + [
        f"HU IP      : {info.ip.ip + ' / ' + info.ip.mask if info.ip else '-'}",
        f"CAF_ID     : {info.caf_id or '-'}",
        f"Coding     : {len(info.coding_datagroups)} datagroup -> headunit/hu_coding_backup.ncd"]


def _scan_section(findings: VehicleFindings) -> List[str]:
    lines = ["", "DID TARAMALARI", "-" * 60]
    for address, stats in sorted(findings.scan_stats.items()):
        lines.append(f"{ecu_label(address)}: okunabilir {stats.positive}, kilitli/kosullu {stats.negative}, "
                     f"yok {stats.not_supported}, cevapsiz {stats.silent}")
    return lines


def _live_section(findings: VehicleFindings) -> List[str]:
    lines = ["", "CANLI KAYIT", "-" * 60]
    for address, stats in sorted(findings.live_stats.items()):
        lines.append(f"{ecu_label(address)}: {stats.cycles} tur, {stats.did_count} DID, {stats.seconds} sn, "
                     f"degisen {len(stats.changing_dids)} DID")
        if stats.changing_dids:
            lines.append("      " + " ".join(f"{d:04X}" for d in stats.changing_dids))
        if stats.key_ranges:
            lines.append("  Bilinen degerler (topluluk B58 haritasina gore cozuldu, bu arabada DOGRULANMADI):")
            lines += [f"      {r.did:04X} {r.name:<42} min {r.minimum:>10} max {r.maximum:>10} {r.unit}"
                      f" ({r.samples} ornek)" for r in stats.key_ranges]
    return lines
