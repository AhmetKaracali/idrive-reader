"""Pure functions that turn raw UDS response payloads into typed values."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import List, Optional, Tuple

from third_party.nbtevo.uds import decode_svk

READ_DTC_POSITIVE = 0x59


@dataclass(frozen=True)
class SvkEntry:
    process_class: str
    id: str
    version: str

    @property
    def text(self) -> str:
        return f"{self.process_class}-{self.id}-{self.version}"


@dataclass(frozen=True)
class Dtc:
    code: str
    status: str
    is_active: bool
    is_confirmed: bool


@dataclass(frozen=True)
class FaultMemory:
    availability_mask: str
    dtcs: Tuple[Dtc, ...]


@dataclass(frozen=True)
class ModuleIp:
    version: int
    ip: str
    mask: str
    gateway: str


def ascii_preview(value: bytes) -> str:
    """Printable characters kept, everything else shown as '.'."""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in value)


def decode_svk_entries(value: bytes) -> List[SvkEntry]:
    """Decode an SVK (``22 F1 01``) payload. Raises ``ValueError`` on a malformed table."""
    return [SvkEntry(e.process_class, e.id_hex, e.version) for e in decode_svk(value).entries]


def decode_dtc_response(response: bytes) -> FaultMemory:
    """Decode a full ``59 02`` ReadDTCInformation response. Raises ``ValueError`` if it is not one."""
    if len(response) < 3 or response[0] != READ_DTC_POSITIVE:
        raise ValueError(f"not a 19 02 response: {response.hex()}")
    body = response[3:]
    dtcs = tuple(
        Dtc(code=f"{int.from_bytes(body[i:i + 3], 'big'):06X}", status=f"{body[i + 3]:02X}",
            is_active=bool(body[i + 3] & 0x01), is_confirmed=bool(body[i + 3] & 0x08))
        for i in range(0, len(body) - len(body) % 4, 4))
    return FaultMemory(f"{response[2]:02X}", dtcs)


def decode_module_ip(value: bytes) -> Optional[ModuleIp]:
    """Decode DID ``0x172A`` (``ver | ip4 | mask4 | gw4``); ``None`` if too short."""
    if len(value) < 13:
        return None
    as_ip = lambda raw: str(ipaddress.IPv4Address(raw))  # noqa: E731
    return ModuleIp(value[0], as_ip(value[1:5]), as_ip(value[5:9]), as_ip(value[9:13]))
