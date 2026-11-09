"""End-to-end and unit tests. Run:  .venv\\Scripts\\python -m unittest discover -s tests -v"""
from __future__ import annotations

import ast
import json
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from idrivereader.capture.context import StepStatus  # noqa: E402
from idrivereader.capture.plan import CaptureOptions  # noqa: E402
from idrivereader.capture.service import CaptureService  # noqa: E402
from idrivereader.capture.store import CaptureStore  # noqa: E402
from idrivereader.launch import capture_demo, capture_vehicle  # noqa: E402
from idrivereader.domain.decoders import decode_dtc_response, decode_module_ip, decode_svk_entries  # noqa: E402
from idrivereader.simulator.server import SimulatorServer  # noqa: E402
from idrivereader.simulator.vehicles import RecordedVehicle, demo_vehicle  # noqa: E402
from idrivereader.transport.discovery import DiscoveryMethod, GatewayLocator, VehicleEndpoint  # noqa: E402
from idrivereader.transport.hsfz import HsfzConnection, HsfzFrame  # noqa: E402
from idrivereader.transport.read_only_policy import ReadOnlyViolation, ensure_read_only  # noqa: E402
from idrivereader.transport.uds_client import NoResponseError, UdsClient  # noqa: E402


class QuietProgress:
    def __init__(self):
        self.messages = []
        self.sessions = []

    def step_started(self, title):
        self.messages.append(title)

    def step_progress(self, fraction, detail):
        pass

    def log(self, message):
        self.messages.append(message)

    def step_finished(self, result):
        self.messages.append(f"{result.title}:{result.outcome.status}")

    def session_progress(self, status):
        self.sessions.append(status)


class AlwaysYes:
    def confirm(self, title, message):
        return True

    def inform(self, title, message):
        pass


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(**overrides):
    defaults = dict(progress=QuietProgress(), operator=AlwaysYes(), cancel_requested=threading.Event())
    return {**defaults, **overrides}


class ReadOnlyPolicyTest(unittest.TestCase):
    def test_allows_reads(self):
        for request in (b"\x22\xf1\x90", b"\x22\xf1\x90\xf1\x01", b"\x19\x02\xff", b"\x3e\x00", b"\x3e\x80"):
            ensure_read_only(request)

    def test_blocks_everything_that_can_change_state(self):
        blocked = [b"\x10\x03", b"\x11\x01", b"\x14\xff\xff\xff", b"\x27\x01", b"\x2e\x30\x00\x00",
                   b"\x2f\x00\x01\x03", b"\x31\x01\x0f\x0c", b"\x34\x00", b"\x36\x01", b"\x85\x02",
                   b"\x28\x01\x01", b"\x19\x04\x00\x00\x00\xff", b"\x3e\x01", b"\x22\xf1", b""]
        for request in blocked:
            with self.subTest(request=request.hex()):
                with self.assertRaises(ReadOnlyViolation):
                    ensure_read_only(request)

    def test_client_never_transmits_a_blocked_request(self):
        sent = []

        class Recording:
            def send(self, frame):
                sent.append(frame)

            def receive(self, timeout):
                return None

            def close(self):
                pass

        client = UdsClient(lambda: Recording())
        for request in (b"\x2e\x12\x34\x00", b"\x14\xff\xff\xff", b"\x31\x01\x0f\x0c"):
            with self.assertRaises(ReadOnlyViolation):
                client.request(0x12, request)
            with self.assertRaises(ReadOnlyViolation):
                client.request_functional(request)
        self.assertEqual(sent, [])


class ArchitectureTest(unittest.TestCase):
    """Layer rules: only the transport layer touches sockets; lower layers never import upper ones."""

    PACKAGE = SRC / "idrivereader"

    def _imports(self, path: Path) -> set:
        """Fully qualified modules imported by ``path`` (relative imports resolved)."""
        module_parts = list(path.relative_to(SRC).with_suffix("").parts)
        package = module_parts[:-1] if module_parts[-1] != "__init__" else module_parts[:-1]
        found = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join(base + ([node.module] if node.module else []))
                found.add(module)
                found.update(f"{module}.{alias.name}" for alias in node.names)
        return found

    def _check(self, folder: str, forbidden_prefixes: tuple) -> None:
        for path in (self.PACKAGE / folder).rglob("*.py"):
            for module in self._imports(path):
                with self.subTest(file=str(path.relative_to(SRC)), imports=module):
                    self.assertFalse(module.startswith(forbidden_prefixes), module)

    def test_steps_and_gui_cannot_reach_the_socket(self):
        forbidden = ("socket", "idrivereader.transport.hsfz")
        self._check("capture/steps", forbidden)
        self._check("gui", forbidden)

    def test_dependency_direction(self):
        upper = ("idrivereader.capture", "idrivereader.gui", "idrivereader.launch", "idrivereader.selftest")
        self._check("domain", upper + ("idrivereader.transport", "idrivereader.simulator"))
        self._check("transport", upper + ("idrivereader.simulator",))
        self._check("simulator", upper)
        self._check("capture", ("idrivereader.gui", "idrivereader.launch", "idrivereader.simulator"))


class ProgressEstimateTest(unittest.TestCase):
    def test_time_left_uses_measured_speed_and_step_fraction(self):
        from idrivereader.capture.session import SessionProgressTracker

        class Step:
            def __init__(self, title, requests):
                self.title, self.requests = title, requests

            def estimated_seconds(self, findings, seconds_per_request):
                return self.requests * seconds_per_request

        now = [0.0]
        tracker = SessionProgressTracker([Step("a", 100), Step("b", 300)], findings=None,
                                         measured_seconds_per_request=lambda: 0.01, clock=lambda: now[0])
        tracker.start_step(0)
        tracker.update_step(0.5)
        now[0] = 0.5
        status = tracker.status()
        self.assertEqual((status.step_number, status.step_count), (1, 2))
        self.assertAlmostEqual(status.remaining_seconds, 0.5 + 3.0)
        self.assertAlmostEqual(status.percent, 100 * 0.5 / 4.0, places=1)

    def test_duration_format(self):
        from idrivereader.gui.app import format_duration
        self.assertEqual(format_duration(0), "bitti")
        self.assertEqual(format_duration(42), "~40 sn")
        self.assertEqual(format_duration(12 * 60 + 10), "~12 dk")
        self.assertEqual(format_duration(65 * 60), "~1 sa 5 dk")


class DecoderTest(unittest.TestCase):
    def test_dtc_response(self):
        memory = decode_dtc_response(bytes.fromhex("5902ff801c2008b7f33009"))
        self.assertEqual([d.code for d in memory.dtcs], ["801C20", "B7F330"])
        self.assertTrue(memory.dtcs[1].is_active)

    def test_module_ip(self):
        self.assertEqual(decode_module_ip(bytes([1, 169, 254, 1, 2, 255, 255, 0, 0, 0, 0, 0, 0])).ip,
                         "169.254.1.2")

    def test_measurement_scaling(self):
        from idrivereader.domain.measurements import MEASUREMENTS_BY_DID
        self.assertAlmostEqual(MEASUREMENTS_BY_DID[0x4300].decode(bytes.fromhex("0DBA")), 78.26, places=2)
        self.assertAlmostEqual(MEASUREMENTS_BY_DID[0x59FA].decode(bytes([0xF4])), -9.0)  # signed char
        self.assertIsNone(MEASUREMENTS_BY_DID[0x4205].decode(b"\x01"))  # too short

    def test_svk(self):
        value = demo_vehicle().respond(0x63, b"\x22\xf1\x01")[0][1][3:]
        self.assertIn("BTLD-00001FFD-007.051.002", [e.text for e in decode_svk_entries(value)])


class MacOsDiscoveryTest(unittest.TestCase):
    def test_parse_ifconfig_finds_link_local_per_interface(self):
        from idrivereader.transport.discovery import parse_ifconfig
        output = ("lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
                  "\tinet 127.0.0.1 netmask 0xff000000\n"
                  "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                  "\tether 3c:22:fb:00:00:01\n"
                  "\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255\n"
                  "en7: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                  "\tinet6 fe80::1%en7 prefixlen 64 scopeid 0x10\n"
                  "\tinet 169.254.33.7 netmask 0xffff0000 broadcast 169.254.255.255\n")
        addresses = parse_ifconfig(output)
        self.assertIn(("169.254.33.7", "en7"), [(a.ip, a.interface) for a in addresses])
        self.assertIn(("192.168.1.20", "en0"), [(a.ip, a.interface) for a in addresses])
        self.assertEqual(len(addresses), 3)


class TransportTest(unittest.TestCase):
    def test_ident_discovery_on_localhost(self):
        ident_port = _free_udp_port()
        with SimulatorServer(demo_vehicle(), ident_port=ident_port) as server:
            locator = GatewayLocator(ident_port=ident_port, diag_port=server.diag_port, listen_seconds=1.0)
            endpoints = locator.locate("127.0.0.1", broadcast_targets=("127.0.0.1",))
        self.assertEqual(len(endpoints), 1)
        self.assertTrue(endpoints[0].is_gateway)
        self.assertEqual(endpoints[0].vin, "DEMOVIN0000000001")

    def test_silent_target_times_out_and_payload_strips_echo(self):
        with SimulatorServer(demo_vehicle()) as server:
            client = UdsClient(lambda: HsfzConnection("127.0.0.1", server.diag_port))
            with self.assertRaises(NoResponseError):
                client.request(0x55, b"\x3e\x00", timeout=0.2)
            self.assertEqual(client.request(0x12, b"\x22\xf1\x90").payload, b"DEMOVIN0000000001")
            client.close()

    def test_connection_refuses_a_write_even_without_the_client(self):
        with SimulatorServer(demo_vehicle()) as server:
            connection = HsfzConnection("127.0.0.1", server.diag_port)
            with self.assertRaises(ReadOnlyViolation):
                connection.send(HsfzFrame.uds(0xF4, 0x12, bytes([0x2E, 0x30, 0x00, 0x01])))
            connection.close()

    def test_reconnects_when_the_gateway_drops_the_session(self):
        with SimulatorServer(demo_vehicle()) as server:
            connections = []

            def connect():
                connections.append(HsfzConnection("127.0.0.1", server.diag_port))
                return connections[-1]

            client = UdsClient(connect)
            self.assertTrue(client.request(0x12, b"\x22\xf1\x90").is_positive)
            connections[-1]._sock.close()  # simulate the gateway closing the TCP session
            self.assertTrue(client.request(0x12, b"\x22\xf1\x90").is_positive)
            self.assertEqual(len(connections), 2)
            client.close()


class EndToEndTest(unittest.TestCase):
    def _capture(self, vehicle, root: Path):
        options = CaptureOptions().for_demo()
        options = CaptureOptions(live_seconds=3, did_range=options.did_range,
                                 inventory_sweep_timeout=options.inventory_sweep_timeout)
        with SimulatorServer(vehicle) as server:
            endpoint = VehicleEndpoint(ip="127.0.0.1", port=server.diag_port, diag_address=0x10, found_by=DiscoveryMethod.DEMO)
            return CaptureService(root).run(endpoint, options, **_run())

    def test_full_session_against_demo_car_then_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._capture(demo_vehicle(), Path(tmp) / "first")
            self.assertTrue(result.all_ok, [(r.title, r.outcome) for r in result.results])
            folder = result.folder
            self.assertIn("DEMOVIN0000000001", folder.name)
            for name in ("RAPOR.txt", "session.json", "vehicle.json", "ecus.json", "software.json",
                         "dtcs.json", "frames.jsonl", "log.txt", "headunit/hu.json",
                         "headunit/hu_coding_backup.ncd", "didscan/DME_0x12.jsonl",
                         "live/DME_0x12.jsonl", "live/DME_0x12_summary.json"):
                self.assertTrue((folder / name).exists(), name)
            self.assertTrue(zipfile.is_zipfile(result.archive))

            ecus = [e["address"] for e in json.loads((folder / "ecus.json").read_text())]
            self.assertEqual(ecus, ["0x10", "0x12", "0x18", "0x60", "0x63"])
            dtcs = json.loads((folder / "dtcs.json").read_text())
            self.assertEqual(dtcs["0x12"]["reading"]["memory"]["dtcs"][0]["code"], "801C20")
            scan = [json.loads(line) for line in (folder / "didscan/DME_0x12.jsonl").read_text().splitlines()]
            self.assertEqual({r["did"] for r in scan if r["status"] == "positive"},
                             {"4205", "4300", "4402", "4807", "550D"})
            self.assertEqual([r["nrc"] for r in scan if r["status"] == "negative"], ["33", "22"])
            live = json.loads((folder / "live/DME_0x12_summary.json").read_text())
            self.assertGreater(live["rounds"], 1)
            self.assertNotIn("550D", live["changing_dids"])  # constant value must not be "changing"
            # the engine-off "conditionsNotCorrect" DID is sampled during the live capture too
            self.assertIn("59FA", live["priority_dids"])
            ranges = {r["did"]: r for r in live["key_ranges"]}
            self.assertAlmostEqual(ranges[0x550D]["minimum"], 30.196, places=2)
            self.assertTrue(750 <= ranges[0x4807]["minimum"] <= ranges[0x4807]["maximum"] <= 4000)
            self.assertTrue(85 <= ranges[0x4300]["minimum"] <= 105.1)
            ncd = (folder / "headunit/hu_coding_backup.ncd").read_text().splitlines()
            self.assertTrue(ncd[0].startswith("S0") and ncd[-1].startswith("S7"))
            self.assertTrue(all(len(line) <= 80 for line in ncd))
            session = json.loads((folder / "session.json").read_text())
            self.assertEqual(session["vin"], "DEMOVIN0000000001")
            self.assertTrue(all(step["status"] == "ok" for step in session["steps"]))
            report = (folder / "RAPOR.txt").read_text(encoding="utf-8")
            self.assertIn("HICBIR yazma", report)
            self.assertIn("801C20", report)
            self.assertNotIn("RAPOR olusturulamadi", report)

            replayed = self._capture(RecordedVehicle.from_capture(folder), Path(tmp) / "replay")
            replay_scan = (replayed.folder / "didscan/DME_0x12.jsonl").read_text().splitlines()
            self.assertEqual(sorted(json.loads(l)["did"] for l in replay_scan), sorted(r["did"] for r in scan))
            self.assertEqual(json.loads((replayed.folder / "dtcs.json").read_text())["0x12"]["reading"],
                             dtcs["0x12"]["reading"])

    def test_stop_keeps_partial_data(self):
        cancel = threading.Event()

        class StopDuringScan(QuietProgress):
            def step_started(self, title):
                if title.startswith("DID taramasi"):
                    cancel.set()

        with tempfile.TemporaryDirectory() as tmp:
            result = capture_demo(Path(tmp), CaptureOptions(),
                                  **_run(progress=StopDuringScan(), cancel_requested=cancel))
            statuses = [r.outcome.status for r in result.results]
            self.assertEqual(statuses[:5], [StepStatus.OK] * 5)
            self.assertTrue(all(s is StepStatus.CANCELLED for s in statuses[5:]))
            self.assertTrue((result.folder / "RAPOR.txt").exists())

    def test_rename_and_zip_failures_do_not_lose_the_capture(self):
        def refuse(*args, **kwargs):
            raise PermissionError(32, "in use by another process")

        originals = (CaptureStore.rename_folder, CaptureStore.archive)
        CaptureStore.rename_folder, CaptureStore.archive = refuse, refuse
        try:
            with tempfile.TemporaryDirectory() as tmp:
                progress = QuietProgress()
                result = capture_demo(Path(tmp), CaptureOptions(live_seconds=0), **_run(progress=progress))
                self.assertTrue(result.all_ok)
                self.assertIsNone(result.archive)
                self.assertTrue((result.folder / "RAPOR.txt").exists())
                self.assertTrue(any("Zip olusturulamadi" in m for m in progress.messages))
        finally:
            CaptureStore.rename_folder, CaptureStore.archive = originals

    def test_overall_progress_reaches_100_and_counts_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            progress = QuietProgress()
            result = capture_demo(Path(tmp), CaptureOptions(live_seconds=2), **_run(progress=progress))
            self.assertTrue(result.all_ok)
            statuses = progress.sessions
            self.assertGreater(len(statuses), len(result.results))
            self.assertEqual({s.step_count for s in statuses}, {len(result.results)})
            numbers = [s.step_number for s in statuses]
            self.assertEqual(numbers, sorted(numbers))
            self.assertEqual((statuses[-1].percent, statuses[-1].remaining_seconds), (100.0, 0.0))
            self.assertTrue(all(0 <= s.percent <= 100 and s.remaining_seconds >= 0 for s in statuses))

    def test_no_car_found_returns_none(self):
        class NoCar:
            def find(self, wait_seconds, log, cancel_requested):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            progress = QuietProgress()
            self.assertIsNone(capture_vehicle(Path(tmp), CaptureOptions(), manual_ip=None, finder=NoCar(),
                                              **_run(progress=progress)))
            self.assertTrue(any("bulunamadi" in m for m in progress.messages))


if __name__ == "__main__":
    unittest.main()
