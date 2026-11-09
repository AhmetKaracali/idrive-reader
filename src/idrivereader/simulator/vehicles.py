"""
Vehicle models the simulator server can answer for.

* :class:`ScriptedVehicle` — a hand-built car (tests and the GUI's demo mode).
* :class:`RecordedVehicle` — answers exactly what a real car answered in a capture folder's
  ``frames.jsonl``; repeated requests cycle through the recorded answers, so live values change
  the way they did in the car.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Set, Tuple, Union

from ..transport.hsfz import CTRL_DATA, FUNCTIONAL_ADDRESS, TESTER_ADDRESS
from ..transport.recorder import FRAMES_FILE, read_frames
from ..transport.uds import (NRC_CONDITIONS_NOT_CORRECT, NRC_REQUEST_OUT_OF_RANGE, NRC_SECURITY_ACCESS_DENIED,
                              NRC_SERVICE_NOT_SUPPORTED, SID_READ_DATA_BY_ID,
                              SID_READ_DTC_INFORMATION, SID_TESTER_PRESENT, is_response_pending,
                              is_response_to, negative_response, positive_response)

Answer = Tuple[int, bytes]  # (source address, UDS response)
_ALL_STATUS_BITS = 0xFF  # DTC status availability mask the demo car reports
DidValue = Union[bytes, Callable[[], bytes]]


class VehicleModel(Protocol):
    def ident(self) -> bytes: ...

    def respond(self, target: int, request: bytes) -> List[Answer]: ...


class ScriptedVehicle:
    def __init__(self, vin: str, dids: Dict[int, Dict[int, DidValue]],
                 dtcs: Optional[Dict[int, List[Tuple[int, int]]]] = None,
                 locked: Optional[Dict[int, Set[int]]] = None,
                 conditional: Optional[Dict[int, Set[int]]] = None):
        self.vin = vin
        self._dids = dids
        self._dtcs = dtcs or {}
        self._locked = locked or {}  # answer securityAccessDenied
        self._conditional = conditional or {}  # answer conditionsNotCorrect (e.g. needs engine running)

    def ident(self) -> bytes:
        return f"DIAGADR10BMWMAC001A37000000BMWVIN{self.vin}".encode("latin1")

    def respond(self, target: int, request: bytes) -> List[Answer]:
        if target == FUNCTIONAL_ADDRESS:
            if request[0] == SID_TESTER_PRESENT:
                return [(address, positive_response(request)) for address in sorted(self._dids)]
            return []
        if target not in self._dids:
            return []
        return [(target, self._answer(target, request))]

    def _answer(self, target: int, request: bytes) -> bytes:
        sid = request[0]
        if sid == SID_TESTER_PRESENT:
            return positive_response(request)
        if sid == SID_READ_DTC_INFORMATION:
            body = b"".join(code.to_bytes(3, "big") + bytes([status])
                            for code, status in self._dtcs.get(target, []))
            return positive_response(request, bytes([_ALL_STATUS_BITS]) + body)
        if sid == SID_READ_DATA_BY_ID:
            did = int.from_bytes(request[1:3], "big")
            if did in self._locked.get(target, set()):
                return negative_response(sid, NRC_SECURITY_ACCESS_DENIED)
            if did in self._conditional.get(target, set()):
                return negative_response(sid, NRC_CONDITIONS_NOT_CORRECT)
            value = self._dids[target].get(did)
            if value is None:
                return negative_response(sid, NRC_REQUEST_OUT_OF_RANGE)
            return positive_response(request, value() if callable(value) else value)
        return negative_response(sid, NRC_SERVICE_NOT_SUPPORTED)


def _sgbm(process_class: int, identifier: int, version: Tuple[int, int, int]) -> bytes:
    return bytes([process_class]) + identifier.to_bytes(4, "big") + bytes(version)


def _svk(*entries: bytes) -> bytes:
    return bytes([0x01, 0x01]) + len(entries).to_bytes(2, "big") + b"\x00" * 13 + b"".join(entries)


def _wave(period: float, low: float, high: float, scale: float, size: int = 2) -> Callable[[], bytes]:
    def value() -> bytes:
        phase = (math.sin(time.time() * 2 * math.pi / period) + 1) / 2
        return int((low + phase * (high - low)) * scale).to_bytes(size, "big")
    return value


def demo_vehicle() -> ScriptedVehicle:
    """A small fake F30 for trying the program without a car. Every value is invented."""
    common = {0xF190: b"DEMOVIN0000000001", 0xF18C: b"SN-DEMO-0001"}
    hu_coding = {dg: bytes((dg + i) & 0xFF for i in range(48)) for dg in range(0x3000, 0x300E)}
    hu_coding[0x300F] = _sgbm(0x05, 0x00001EF6, (6, 48, 13))
    hu_coding[0x3010] = bytes(range(128))
    dids = {
        0x10: {**common, 0xF101: _svk(_sgbm(0x06, 0x00001A2B, (1, 2, 3)), _sgbm(0x08, 0x00001A2C, (1, 2, 3)))},
        0x12: {**common, 0xF101: _svk(_sgbm(0x06, 0x00002B3C, (4, 5, 6)), _sgbm(0x05, 0x00002B3D, (4, 5, 6))),
               # Raw encodings follow domain/measurements.py, so decoded values look plausible.
               0x4807: _wave(5, 750, 4000, 2),                    # rpm (raw = rpm * 2)
               0x4300: _wave(60, 85 + 273.14, 105 + 273.14, 10),  # coolant C (raw = (C + 273.14) * 10)
               0x4402: _wave(90, (95 + 48) / 0.75, (115 + 48) / 0.75, 1),  # oil C (raw = (C + 48) / 0.75)
               0x4205: _wave(8, 1000, 2300, 256 / 20),            # boost hPa (raw = hPa * 256 / 20)
               0x550D: bytes([77])},                              # ethanol ~30 % (constant)
        0x18: {**common, 0xF101: _svk(_sgbm(0x06, 0x00003C4D, (7, 8, 9)))},
        0x60: {**common, 0xF101: _svk(_sgbm(0x06, 0x00004D5E, (2, 0, 1)))},
        0x63: {**common, **hu_coding,
               0xF101: _svk(_sgbm(0x06, 0x00001FFD, (7, 51, 2)), _sgbm(0x05, 0x00001EF6, (6, 48, 13)),
                            _sgbm(0x08, 0x0000271A, (7, 51, 2))),
               0x172A: bytes([1, 169, 254, 30, 40, 255, 255, 0, 0, 0, 0, 0, 0]),
               0xFDEC: bytes([0x01, 0x43, 0x01])},
    }
    return ScriptedVehicle("DEMOVIN0000000001", dids, dtcs={0x12: [(0x801C20, 0x08)], 0x63: [(0xB7F330, 0x09)]},
                           locked={0x12: {0x5000}}, conditional={0x12: {0x59FA}})


class RecordedVehicle:
    def __init__(self, ident_text: str, answers: Dict[Tuple[int, bytes], List[bytes]],
                 functional: Dict[bytes, List[Answer]]):
        self._ident = ident_text.encode("latin1")
        self._answers = answers
        self._functional = functional
        self._cursor: Dict[Tuple[int, bytes], int] = defaultdict(int)

    @classmethod
    def from_capture(cls, folder: Path) -> "RecordedVehicle":
        answers: Dict[Tuple[int, bytes], List[bytes]] = defaultdict(list)
        functional: Dict[bytes, List[Answer]] = defaultdict(list)
        pending: Optional[Tuple[int, bytes]] = None
        for frame in read_frames(folder / FRAMES_FILE):
            if frame.control != CTRL_DATA:
                continue
            if frame.direction == "tx":
                pending = (frame.target, frame.data)
                continue
            if (pending is None or frame.target != TESTER_ADDRESS
                    or not is_response_to(frame.data, pending[1]) or is_response_pending(frame.data)):
                continue
            target, request = pending
            if target == FUNCTIONAL_ADDRESS:
                functional[request].append((frame.source, frame.data))
            elif frame.source == target:
                answers[(target, request)].append(frame.data)
        return cls(cls._ident_of(folder), dict(answers), dict(functional))

    @staticmethod
    def _ident_of(folder: Path) -> str:
        """The HSFZ ident text recorded by the identity step (top-level ``ident`` in vehicle.json)."""
        vehicle = folder / "vehicle.json"
        if not vehicle.exists():
            return "DIAGADR10"
        info = json.loads(vehicle.read_text(encoding="utf-8"))
        return info.get("ident") or f"DIAGADR10BMWVIN{info.get('vin') or ''}"

    def ident(self) -> bytes:
        return self._ident

    def respond(self, target: int, request: bytes) -> List[Answer]:
        if target == FUNCTIONAL_ADDRESS:
            return list(self._functional.get(request, []))
        recorded = self._answers.get((target, request))
        if not recorded:
            known_target = any(key[0] == target for key in self._answers)
            if known_target and request[0] == SID_READ_DATA_BY_ID:
                return [(target, negative_response(SID_READ_DATA_BY_ID, NRC_REQUEST_OUT_OF_RANGE))]
            return []
        key = (target, request)
        answer = recorded[self._cursor[key] % len(recorded)]
        self._cursor[key] += 1
        return [(target, answer)]
