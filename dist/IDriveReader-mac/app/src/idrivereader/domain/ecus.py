"""Well-known F-series diagnostic addresses and identifier ranges. Names are report labels only."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ecu:
    address: int
    short_label: str  # used in file names, e.g. didscan/DME_0x12.jsonl


GATEWAY = Ecu(0x10, "ZGM")
DME = Ecu(0x12, "DME")
HEAD_UNIT = Ecu(0x63, "HU")
FUNCTIONAL_ADDRESS = 0xDF  # broadcast address every ECU listens on

# Standard identification DIDs (VIN, SVK, part numbers...), end exclusive.
IDENTIFICATION_DIDS = range(0xF100, 0xF200)

_NAMES = {
    0x01: "ACSM (airbag)",
    GATEWAY.address: "ZGM/FEM (gateway)",
    DME.address: "DME (motor)",
    0x18: "EGS (sanziman)",
    0x29: "DSC (fren/stabilite)",
    0x30: "EPS (direksiyon)",
    0x40: "FEM_BODY (govde)",
    0x60: "KOMBI (gosterge)",
    HEAD_UNIT.address: "HU (NBT EVO)",
}


def ecu_name(address: int) -> str:
    return _NAMES.get(address, "Bilinmeyen modul")


def ecu_label(address: int) -> str:
    return f"0x{address:02X} {ecu_name(address)}"
