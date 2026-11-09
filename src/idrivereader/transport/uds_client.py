"""
A UDS request/response client over HSFZ that only ever sends read-only requests.

Responsibilities:
* enforce :func:`ensure_read_only` before anything is transmitted (not configurable);
* match each response to its request by source address, service id and identifier echo, so a late
  answer to an earlier timed-out request is never mistaken for the current one;
* honour ``7F xx 78`` (responsePending) by extending the wait;
* reconnect and repeat the request when the TCP session drops (reads are safe to repeat).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .hsfz import CTRL_DATA, FUNCTIONAL_ADDRESS, TESTER_ADDRESS, Connection, HsfzFrame
from .read_only_policy import ensure_read_only
from .uds import (ECHO_LENGTH, NEGATIVE_RESPONSE, NRC_CONDITIONS_NOT_CORRECT, NRC_REQUEST_OUT_OF_RANGE,
                  POSITIVE_OFFSET, is_response_pending, is_response_to)

ConnectionFactory = Callable[[], Connection]
_MIN_REQUESTS_FOR_AVERAGE = 20


@dataclass(frozen=True)
class UdsResponse:
    target: int
    request: bytes
    raw: bytes

    @property
    def is_positive(self) -> bool:
        return bool(self.raw) and self.raw[0] == self.request[0] + POSITIVE_OFFSET

    @property
    def negative_code(self) -> Optional[int]:
        if len(self.raw) >= 3 and self.raw[0] == NEGATIVE_RESPONSE:
            return self.raw[2]
        return None

    @property
    def is_out_of_range(self) -> bool:
        return self.negative_code == NRC_REQUEST_OUT_OF_RANGE

    @property
    def is_conditions_not_correct(self) -> bool:
        """The identifier exists but is not readable in the current state (e.g. engine off)."""
        return self.negative_code == NRC_CONDITIONS_NOT_CORRECT

    @property
    def payload(self) -> bytes:
        """The data after the SID and the echoed DID/sub-function of a positive response."""
        if not self.is_positive:
            raise ValueError("a negative response has no payload")
        return self.raw[1 + ECHO_LENGTH.get(self.request[0], 0):]


class NoResponseError(TimeoutError):
    def __init__(self, target: int, request: bytes):
        super().__init__(f"no answer from 0x{target:02X} to {request.hex(' ').upper()}")
        self.target = target
        self.request = request


class UdsClient:
    def __init__(self, connect: ConnectionFactory, response_pending_timeout: float = 6.0,
                 max_reconnects: int = 3):
        self._connect = connect
        self._pending_timeout = response_pending_timeout
        self._max_reconnects = max_reconnects
        self._connection: Optional[Connection] = None
        self._timed_requests = 0
        self._timed_seconds = 0.0

    @property
    def average_request_seconds(self) -> Optional[float]:
        """Measured wall time per answered single-ECU request; ``None`` until there is enough data."""
        if self._timed_requests < _MIN_REQUESTS_FOR_AVERAGE:
            return None
        return self._timed_seconds / self._timed_requests

    def request(self, target: int, request: bytes, timeout: float = 2.0) -> UdsResponse:
        """Send ``request`` to one ECU and return its final answer (positive or negative)."""
        ensure_read_only(request)
        frame = HsfzFrame.uds(TESTER_ADDRESS, target, request)
        started = time.monotonic()
        response = self._with_reconnect(lambda connection: self._exchange(connection, frame, timeout))
        # Only answered requests count: timeouts are rare in a scan but would skew the average.
        self._timed_requests += 1
        self._timed_seconds += time.monotonic() - started
        return response

    def request_functional(self, request: bytes, window: float = 2.0) -> Dict[int, UdsResponse]:
        """Broadcast to the functional address and collect every ECU's first answer."""
        ensure_read_only(request)
        frame = HsfzFrame.uds(TESTER_ADDRESS, FUNCTIONAL_ADDRESS, request)
        return self._with_reconnect(lambda connection: self._collect(connection, frame, window))

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _exchange(self, connection: Connection, frame: HsfzFrame, timeout: float) -> UdsResponse:
        connection.send(frame)
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            reply = connection.receive(remaining)
            if reply is None:
                break
            if not self._is_reply(reply, frame.target) or not is_response_to(reply.data, frame.data):
                continue
            if is_response_pending(reply.data):
                deadline = time.monotonic() + self._pending_timeout
                continue
            return UdsResponse(frame.target, frame.data, reply.data)
        raise NoResponseError(frame.target, frame.data)

    @staticmethod
    def _collect(connection: Connection, frame: HsfzFrame, window: float) -> Dict[int, UdsResponse]:
        connection.send(frame)
        answers: Dict[int, UdsResponse] = {}
        deadline = time.monotonic() + window
        while (remaining := deadline - time.monotonic()) > 0:
            reply = connection.receive(remaining)
            if reply is None:
                break
            if (reply.control == CTRL_DATA and reply.target == TESTER_ADDRESS
                    and reply.source not in (TESTER_ADDRESS, FUNCTIONAL_ADDRESS)
                    and is_response_to(reply.data, frame.data) and reply.source not in answers):
                answers[reply.source] = UdsResponse(reply.source, frame.data, reply.data)
        return answers

    @staticmethod
    def _is_reply(frame: HsfzFrame, target: int) -> bool:
        return frame.control == CTRL_DATA and frame.source == target and frame.target == TESTER_ADDRESS

    def _with_reconnect(self, exchange):
        last_error: Optional[Exception] = None
        for attempt in range(self._max_reconnects + 1):
            try:
                if self._connection is None:
                    self._connection = self._connect()
                return exchange(self._connection)
            except NoResponseError:
                raise
            except OSError as error:  # ConnectionError/reset/refused; a read is safe to repeat
                last_error = error
                self.close()
                if attempt < self._max_reconnects:
                    time.sleep(1.0)
        raise ConnectionError(f"could not reach the vehicle: {last_error}")
