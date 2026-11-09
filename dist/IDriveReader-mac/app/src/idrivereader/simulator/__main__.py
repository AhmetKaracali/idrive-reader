"""
Run the fake car on this PC so apps can be developed without the real car.

    python -m idrivereader.simulator --capture "kayitlar\\kayit_<VIN>_<tarih>"   (replay a real capture)
    python -m idrivereader.simulator --demo                                      (invented demo car)

The gateway listens on 127.0.0.1:6801 (diagnostics) and 127.0.0.1:6811 (ident), like the car.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .server import SimulatorServer
from .vehicles import RecordedVehicle, demo_vehicle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path, help="a capture folder produced by IDriveReader")
    source.add_argument("--demo", action="store_true", help="serve the built-in demo car")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6801)
    parser.add_argument("--ident-port", type=int, default=6811)
    args = parser.parse_args()

    vehicle = demo_vehicle() if args.demo else RecordedVehicle.from_capture(args.capture)
    with SimulatorServer(vehicle, args.host, args.port, args.ident_port) as server:
        print(f"Sahte arac hazir: {args.host}:{server.diag_port} (ident {args.ident_port}). Durdurmak icin Ctrl+C.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
