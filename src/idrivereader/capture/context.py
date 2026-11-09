"""Shared types every capture step works with."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Dict, FrozenSet, List, Optional, Protocol, Tuple

from ..domain.decoders import FaultMemory, ModuleIp, SvkEntry
from ..transport.discovery import VehicleEndpoint
from ..transport.uds_client import UdsClient
from .store import CaptureStore


class CaptureCancelled(Exception):
    """The operator pressed Stop."""


class StepAborted(Exception):
    """The step cannot continue (for example the ECU stopped answering and the operator gave up)."""


class StepStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class StepOutcome:
    status: StepStatus
    summary: str

    @property
    def is_ok(self) -> bool:
        return self.status is StepStatus.OK


@dataclass(frozen=True)
class StepResult:
    title: str
    outcome: StepOutcome


@dataclass(frozen=True)
class SessionStatus:
    """Where the whole capture stands: step N of M, overall percent and estimated time left."""

    step_number: int
    step_count: int
    step_title: str
    percent: float
    remaining_seconds: float


class ProgressSink(Protocol):
    def step_started(self, title: str) -> None: ...

    def step_progress(self, fraction: float, detail: str) -> None: ...

    def log(self, message: str) -> None: ...

    def step_finished(self, result: StepResult) -> None: ...

    def session_progress(self, status: SessionStatus) -> None: ...


class OperatorPrompt(Protocol):
    def confirm(self, title: str, message: str) -> bool:
        """Show ``message`` and block until the operator answers; True means continue."""

    def inform(self, title: str, message: str) -> None:
        """Show ``message`` and block until the operator acknowledges it."""


@dataclass(frozen=True)
class FaultReading:
    """Exactly one of ``memory`` (the decoded fault memory) or ``error`` is set."""

    memory: Optional[FaultMemory]
    error: Optional[str]

    def __post_init__(self) -> None:
        if (self.memory is None) == (self.error is None):
            raise ValueError("FaultReading needs exactly one of memory or error")

    @classmethod
    def read(cls, memory: FaultMemory) -> "FaultReading":
        return cls(memory, None)

    @classmethod
    def failed(cls, error: str) -> "FaultReading":
        return cls(None, error)


@dataclass(frozen=True)
class HeadUnitInfo:
    ip: Optional[ModuleIp]
    caf_id: Optional[str]
    coding_datagroups: Tuple[int, ...]


@dataclass(frozen=True)
class ScanStats:
    positive: int
    negative: int
    not_supported: int
    silent: int


@dataclass(frozen=True)
class MeasurementRange:
    """Decoded min/max of one well-known measurement during the live capture."""

    did: int
    name: str
    unit: str
    minimum: float
    maximum: float
    samples: int


@dataclass(frozen=True)
class LiveStats:
    cycles: int
    did_count: int
    seconds: float
    changing_dids: Tuple[int, ...]
    key_ranges: Tuple[MeasurementRange, ...] = ()


@dataclass
class VehicleFindings:
    """What the session has learned so far; later steps and the report read it."""

    endpoint: VehicleEndpoint
    vin: Optional[str] = None
    ecus: List[int] = field(default_factory=list)
    software: Dict[int, List[SvkEntry]] = field(default_factory=dict)
    faults: Dict[int, FaultReading] = field(default_factory=dict)
    head_unit: Optional[HeadUnitInfo] = None
    did_values: Dict[int, Dict[int, bytes]] = field(default_factory=dict)
    conditional_dids: Dict[int, FrozenSet[int]] = field(default_factory=dict)  # answered "conditionsNotCorrect"
    scan_stats: Dict[int, ScanStats] = field(default_factory=dict)
    live_stats: Dict[int, LiveStats] = field(default_factory=dict)


@dataclass
class CaptureContext:
    client: UdsClient
    store: CaptureStore
    findings: VehicleFindings
    progress: ProgressSink
    operator: OperatorPrompt
    cancel_requested: threading.Event

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CaptureCancelled` once the operator has pressed Stop."""
        if self.cancel_requested.is_set():
            raise CaptureCancelled()


class CaptureStep(Protocol):
    title: str

    def run(self, context: CaptureContext) -> StepOutcome: ...

    def estimated_seconds(self, findings: VehicleFindings, seconds_per_request: float) -> float:
        """Rough duration of this step, for the overall time-left estimate."""
