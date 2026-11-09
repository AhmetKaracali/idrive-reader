"""Turns the operator's choices into the ordered list of capture steps."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Tuple

from ..domain.ecus import DME, GATEWAY, HEAD_UNIT
from .context import CaptureStep
from .steps.did_scan import DidScanStep
from .steps.faults import FaultMemoryStep
from .steps.head_unit import HeadUnitStep
from .steps.identity import VehicleIdentityStep
from .steps.inventory import EcuInventoryStep
from .steps.live import LiveCaptureStep
from .steps.software import SoftwareVersionsStep

FULL_DID_RANGE = (0x0000, 0xFFFF)  # inclusive
DEMO_DID_RANGE = (0x2F00, 0x5A00)  # covers every identifier the demo car answers outside F1xx
DEMO_MAX_LIVE_SECONDS = 20


@dataclass(frozen=True, kw_only=True)
class CaptureOptions:
    scan_dme: bool = True
    scan_head_unit: bool = True
    scan_gateway: bool = True
    live_seconds: int = 60
    did_range: Tuple[int, int] = FULL_DID_RANGE  # first and last DID, both inclusive
    inventory_sweep_timeout: float = 0.35

    def for_demo(self) -> "CaptureOptions":
        """The same choices, shrunk so a run against the localhost demo car takes about a minute."""
        return replace(self, did_range=DEMO_DID_RANGE, inventory_sweep_timeout=0.02,
                       live_seconds=min(self.live_seconds, DEMO_MAX_LIVE_SECONDS))


def build_steps(options: CaptureOptions) -> List[CaptureStep]:
    """Fixed order: identity, inventory, versions, faults, HU, DME scan, live (engine on), other scans."""
    first, last = options.did_range
    steps: List[CaptureStep] = [VehicleIdentityStep(),
                                EcuInventoryStep(sweep_timeout=options.inventory_sweep_timeout),
                                SoftwareVersionsStep(), FaultMemoryStep(), HeadUnitStep()]
    if options.scan_dme:
        steps.append(DidScanStep(DME, first, last))
        if options.live_seconds > 0:
            steps.append(LiveCaptureStep(DME, options.live_seconds))
    if options.scan_head_unit:
        steps.append(DidScanStep(HEAD_UNIT, first, last))
    if options.scan_gateway:
        steps.append(DidScanStep(GATEWAY, first, last))
    return steps
