"""
The read-only guarantee.

:func:`ensure_read_only` runs inside :class:`~idrivereader.transport.uds_client.UdsClient` before
every transmission; it is not configurable. Only services that cannot change ECU state pass:

* ``0x22`` ReadDataByIdentifier (any identifier)
* ``0x19`` ReadDTCInformation, sub-function ``0x02`` (report DTCs by status mask)
* ``0x3E`` TesterPresent, sub-functions ``0x00`` / ``0x80``

Anything else — session changes, SecurityAccess, writes, routines, resets, clearing DTCs, I/O
control — raises :class:`ReadOnlyViolation` and is never transmitted.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from .uds import SID_READ_DATA_BY_ID, SID_READ_DTC_INFORMATION, SID_TESTER_PRESENT

_ANY_SUBFUNCTION = None
_ALLOWED: Dict[int, Optional[FrozenSet[int]]] = {
    SID_READ_DATA_BY_ID: _ANY_SUBFUNCTION,
    SID_READ_DTC_INFORMATION: frozenset({0x02}),
    SID_TESTER_PRESENT: frozenset({0x00, 0x80}),
}


class ReadOnlyViolation(RuntimeError):
    """Raised when code attempts to send a request that could change vehicle state."""


def ensure_read_only(request: bytes) -> None:
    """Return silently for an allowed request; raise :class:`ReadOnlyViolation` otherwise."""
    if not request:
        raise ReadOnlyViolation("empty UDS request")
    sid = request[0]
    if sid not in _ALLOWED:
        raise ReadOnlyViolation(f"service 0x{sid:02X} is not read-only and is blocked")
    subfunctions = _ALLOWED[sid]
    if subfunctions is _ANY_SUBFUNCTION:
        if sid == SID_READ_DATA_BY_ID and (len(request) < 3 or (len(request) - 1) % 2):
            raise ReadOnlyViolation("malformed ReadDataByIdentifier request")
        return
    if len(request) < 2 or request[1] not in subfunctions:
        raise ReadOnlyViolation(f"sub-function of service 0x{sid:02X} is not on the read-only allow-list")
