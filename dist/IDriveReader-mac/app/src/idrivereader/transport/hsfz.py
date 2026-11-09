"""
HSFZ framing and a buffered TCP connection to a BMW F-series ENET endpoint.

Frame layout (big-endian): ``u32 length`` ``u16 control`` ``payload[length]``.
For data (0x0001) and ack (0x0002) frames the payload starts with ``src`` and ``tgt`` bytes,
followed by the UDS bytes. Every frame sent or received is reported to an optional observer so a
session can be recorded byte-for-byte.
"""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from ..domain.ecus import FUNCTIONAL_ADDRESS  # noqa: F401 - re-exported for transport users
from .read_only_policy import ensure_read_only

DIAG_PORT = 6801
IDENT_PORT = 6811

CTRL_DATA = 0x0001
CTRL_ACK = 0x0002
CTRL_IDENT = 0x0011

TESTER_ADDRESS = 0xF4

_MALFORMED = 0x0000

HEADER = struct.Struct(">IH")
_ADDRESSED_CONTROLS = (CTRL_DATA, CTRL_ACK)


@dataclass(frozen=True)
class HsfzFrame:
    """One HSFZ frame. Data and ack frames must carry ``source`` and ``target``."""

    control: int
    data: bytes
    source: Optional[int] = None
    target: Optional[int] = None

    def __post_init__(self) -> None:
        if self.control in _ADDRESSED_CONTROLS and (self.source is None or self.target is None):
            raise ValueError("HSFZ data/ack frames need a source and a target address")

    @classmethod
    def uds(cls, source: int, target: int, payload: bytes) -> "HsfzFrame":
        return cls(CTRL_DATA, payload, source, target)

    @classmethod
    def ident(cls, payload: bytes = b"") -> "HsfzFrame":
        return cls(CTRL_IDENT, payload)

    def encode(self) -> bytes:
        payload = bytes([self.source, self.target]) + self.data if self.control in _ADDRESSED_CONTROLS else self.data
        return HEADER.pack(len(payload), self.control) + payload

    @classmethod
    def decode(cls, control: int, payload: bytes) -> "HsfzFrame":
        if control in _ADDRESSED_CONTROLS:
            if len(payload) < 2:
                return cls(_MALFORMED, payload)  # too short to carry addresses; callers ignore it
            return cls(control, payload[2:], payload[0], payload[1])
        return cls(control, payload)


def take_frame(buffer: bytearray) -> Optional[HsfzFrame]:
    """Remove and return the first complete frame in ``buffer``; ``None`` if it is incomplete."""
    if len(buffer) < HEADER.size:
        return None
    length, control = HEADER.unpack_from(buffer, 0)
    end = HEADER.size + length
    if len(buffer) < end:
        return None
    payload = bytes(buffer[HEADER.size:end])
    del buffer[:end]
    return HsfzFrame.decode(control, payload)


def decode_datagram(datagram: bytes) -> Optional[HsfzFrame]:
    """Decode one UDP datagram (ident request/reply); ``None`` if it is not a whole frame."""
    return take_frame(bytearray(datagram))


class FrameObserver(Protocol):
    def on_frame(self, direction: str, frame: HsfzFrame, timestamp: float) -> None:
        """Called for every frame; ``direction`` is ``"tx"`` or ``"rx"``."""


class Connection(Protocol):
    def send(self, frame: HsfzFrame) -> None: ...

    def receive(self, timeout: float) -> Optional[HsfzFrame]: ...

    def close(self) -> None: ...


class HsfzConnection:
    """A TCP/6801 HSFZ session. Reads are buffered so a timeout never loses a partial frame."""

    def __init__(self, host: str, port: int = DIAG_PORT,
                 observer: Optional[FrameObserver] = None, connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self._observer = observer
        self._buffer = bytearray()
        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, frame: HsfzFrame) -> None:
        if frame.control == CTRL_DATA:
            ensure_read_only(frame.data)  # defence in depth: the client already checked
        self._sock.sendall(frame.encode())
        self._notify("tx", frame)

    def receive(self, timeout: float) -> Optional[HsfzFrame]:
        """Return the next complete frame, or ``None`` if none arrives within ``timeout``."""
        deadline = time.monotonic() + timeout
        while True:
            frame = take_frame(self._buffer)
            if frame is not None:
                self._notify("rx", frame)
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError(f"HSFZ peer {self.host}:{self.port} closed the connection")
            self._buffer.extend(chunk)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _notify(self, direction: str, frame: HsfzFrame) -> None:
        if self._observer is not None:
            self._observer.on_frame(direction, frame, time.time())
