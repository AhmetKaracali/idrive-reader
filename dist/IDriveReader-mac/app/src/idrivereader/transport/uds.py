"""
UDS protocol facts in one place: service ids, response codes, request builders and how a response
is matched to its request. The read-only allow-list, the client, the capture steps and the
simulator all use this module, so they cannot drift apart.
"""
from __future__ import annotations

from typing import Dict

SID_READ_DATA_BY_ID = 0x22
SID_READ_DTC_INFORMATION = 0x19
SID_TESTER_PRESENT = 0x3E

NEGATIVE_RESPONSE = 0x7F
POSITIVE_OFFSET = 0x40

NRC_CONDITIONS_NOT_CORRECT = 0x22  # e.g. a measurement only available with the engine running
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_RESPONSE_PENDING = 0x78

NRC_NAMES = {
    0x10: "generalReject", NRC_SERVICE_NOT_SUPPORTED: "serviceNotSupported",
    0x12: "subFunctionNotSupported", 0x13: "incorrectMessageLength", 0x14: "responseTooLong",
    NRC_CONDITIONS_NOT_CORRECT: "conditionsNotCorrect", NRC_REQUEST_OUT_OF_RANGE: "requestOutOfRange",
    NRC_SECURITY_ACCESS_DENIED: "securityAccessDenied",
    0x7E: "subFunctionNotSupportedInSession", 0x7F: "serviceNotSupportedInSession",
}

# How many request bytes after the SID a positive response repeats (DID or sub-function).
ECHO_LENGTH: Dict[int, int] = {
    SID_READ_DATA_BY_ID: 2,
    SID_READ_DTC_INFORMATION: 1,
    SID_TESTER_PRESENT: 1,
}

TESTER_PRESENT = bytes([SID_TESTER_PRESENT, 0x00])
READ_DTC_BY_STATUS_ALL = bytes([SID_READ_DTC_INFORMATION, 0x02, 0xFF])


def read_did_request(did: int) -> bytes:
    return bytes([SID_READ_DATA_BY_ID, did >> 8, did & 0xFF])


def nrc_name(code: int) -> str:
    return NRC_NAMES.get(code, f"NRC 0x{code:02X}")


def is_response_to(response: bytes, request: bytes) -> bool:
    """True when ``response`` is a positive or negative answer to ``request``."""
    if not response or not request:
        return False
    if response[0] == NEGATIVE_RESPONSE:
        return len(response) >= 3 and response[1] == request[0]
    if response[0] != request[0] + POSITIVE_OFFSET:
        return False
    echo = ECHO_LENGTH.get(request[0], 0)
    return response[1:1 + echo] == request[1:1 + echo]


def is_response_pending(response: bytes) -> bool:
    return len(response) >= 3 and response[0] == NEGATIVE_RESPONSE and response[2] == NRC_RESPONSE_PENDING


def positive_response(request: bytes, data: bytes = b"") -> bytes:
    """The positive answer to ``request``: SID + 0x40, the echoed DID/sub-function, then ``data``."""
    echo = request[1:1 + ECHO_LENGTH.get(request[0], 0)]
    return bytes([request[0] + POSITIVE_OFFSET]) + echo + data


def negative_response(sid: int, code: int) -> bytes:
    return bytes([NEGATIVE_RESPONSE, sid, code])
