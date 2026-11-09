"""
The two ways a capture starts — against the real car, or against the built-in demo car — shared by
the window and by ``--self-test`` so both exercise exactly the same path.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .domain.ecus import GATEWAY
from .simulator.server import SimulatorServer
from .simulator.vehicles import demo_vehicle
from .transport.discovery import DiscoveryMethod, VehicleEndpoint, VehicleFinder
from .capture.context import OperatorPrompt, ProgressSink
from .capture.plan import CaptureOptions
from .capture.service import CaptureResult, CaptureService

ENET_WAIT_SECONDS = 120


def capture_vehicle(output_root: Path, options: CaptureOptions, *, manual_ip: Optional[str],
                    progress: ProgressSink, operator: OperatorPrompt, cancel_requested: threading.Event,
                    finder: Optional[VehicleFinder] = None) -> Optional[CaptureResult]:
    """Find the car (or use ``manual_ip``) and capture it; ``None`` when no car was found."""
    progress.step_started("Arac araniyor")
    if manual_ip:
        endpoint: Optional[VehicleEndpoint] = VehicleEndpoint(ip=manual_ip, found_by=DiscoveryMethod.MANUAL)
    else:
        endpoint = (finder or VehicleFinder()).find(ENET_WAIT_SECONDS, progress.log, cancel_requested)
    if endpoint is None:
        if not cancel_requested.is_set():
            progress.log("Arac bulunamadi. Kontak acik mi, ENET kablosu takili mi? "
                         "KULLANIM.txt icindeki 'ARAC BULUNAMADI' bolumune bakin.")
        return None
    progress.log(f"Arac bulundu: {endpoint.ip} (VIN {endpoint.vin or 'okunacak'}, yontem: {endpoint.found_by})")
    return CaptureService(output_root, "kayit").run(
        endpoint, options, progress=progress, operator=operator, cancel_requested=cancel_requested)


def capture_demo(output_root: Path, options: CaptureOptions, *, progress: ProgressSink,
                 operator: OperatorPrompt, cancel_requested: threading.Event) -> CaptureResult:
    """Run the whole capture against an invented car served on localhost."""
    progress.log("DENEME MODU: bu bilgisayarda sahte bir arac calistiriliyor. Degerler uydurmadir.")
    with SimulatorServer(demo_vehicle()) as server:
        endpoint = VehicleEndpoint(ip="127.0.0.1", port=server.diag_port,
                                   diag_address=GATEWAY.address, found_by=DiscoveryMethod.DEMO)
        return CaptureService(output_root, "deneme").run(
            endpoint, options.for_demo(), progress=progress, operator=operator,
            cancel_requested=cancel_requested)
