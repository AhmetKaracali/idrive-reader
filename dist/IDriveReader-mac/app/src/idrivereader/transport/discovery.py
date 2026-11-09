"""
Finding the laptop's ENET address and the car's diagnostic endpoints.

1. :class:`LocalAddressFinder` lists the laptop's link-local (169.254.x.x) IPv4 addresses — Windows
   assigns one to the Ethernet port about a minute after the ENET cable is plugged in.
2. :class:`GatewayLocator` broadcasts the HSFZ ident request (UDP 6811). If nothing answers (for
   example because a firewall dropped the replies) it falls back to the ARP table and probes each
   link-local neighbour for an open TCP 6801.
3. :class:`VehicleFinder` combines both and waits for the link to come up.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, List, Optional, Sequence

from ..domain.ecus import GATEWAY
from .hsfz import CTRL_IDENT, DIAG_PORT, IDENT_PORT, HsfzFrame, decode_datagram

LINK_LOCAL_PREFIX = "169.254."
_DEFAULT_BROADCASTS = ("255.255.255.255", "169.254.255.255")
_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
_IFCONFIG_INTERFACE = re.compile(r"^([A-Za-z0-9_.\-]+):\s")
_IFCONFIG_INET = re.compile(r"^\s+inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
_IP_BOUND_IF = 25  # macOS <netinet/in.h>: bind a socket's traffic to one interface index


def _run_hidden(command: Sequence[str], timeout: float) -> str:
    """Run a console command without flashing a window (the GUI build has no console)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    completed = subprocess.run(command, capture_output=True, timeout=timeout,
                               creationflags=flags, check=False)
    return completed.stdout.decode("utf-8", "replace")


@dataclass(frozen=True)
class LocalAddress:
    ip: str
    interface: str


class DiscoveryMethod(StrEnum):
    """How the endpoint was found; shown in the report."""

    IDENT = "ident"
    ARP = "arp"
    MANUAL = "manuel"
    DEMO = "deneme"


@dataclass(frozen=True, kw_only=True)
class VehicleEndpoint:
    ip: str
    port: int = DIAG_PORT
    diag_address: Optional[int] = None
    vin: Optional[str] = None
    ident: str = ""
    found_by: DiscoveryMethod

    @property
    def is_gateway(self) -> bool:
        return self.diag_address == GATEWAY.address


def parse_ifconfig(output: str) -> List[LocalAddress]:
    """IPv4 addresses per interface from macOS/BSD ``ifconfig`` output."""
    addresses = []
    interface = "?"
    for line in output.splitlines():
        header = _IFCONFIG_INTERFACE.match(line)
        if header:
            interface = header.group(1)
            continue
        address = _IFCONFIG_INET.match(line)
        if address:
            addresses.append(LocalAddress(address.group(1), interface))
    return addresses


class LocalAddressFinder:
    def find_link_local_addresses(self) -> List[LocalAddress]:
        found = {address.ip: address for address in [*self._from_powershell(), *self._from_ifconfig()]}
        if sys.platform != "darwin":  # hostname lookups can stall on macOS; ifconfig is enough there
            for ip in self._from_hostname():
                found.setdefault(ip, LocalAddress(ip, "?"))
        return [a for a in found.values() if a.ip.startswith(LINK_LOCAL_PREFIX)]

    @staticmethod
    def _from_ifconfig() -> Iterable[LocalAddress]:
        if sys.platform == "win32":
            return []
        try:
            return parse_ifconfig(_run_hidden(["ifconfig"], timeout=10))
        except (OSError, subprocess.SubprocessError):
            return []

    @staticmethod
    def _from_powershell() -> Iterable[LocalAddress]:
        if sys.platform != "win32":
            return []
        script = ("Get-NetIPAddress -AddressFamily IPv4 | "
                  "Select-Object IPAddress,InterfaceAlias | ConvertTo-Json -Compress")
        try:
            output = _run_hidden(["powershell", "-NoProfile", "-Command", script], timeout=15)
            rows = json.loads(output) if output.strip() else []
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        if isinstance(rows, dict):
            rows = [rows]
        return [LocalAddress(str(r.get("IPAddress")), str(r.get("InterfaceAlias")))
                for r in rows if r.get("IPAddress")]

    @staticmethod
    def _from_hostname() -> List[str]:
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        except OSError:
            return []
        return sorted({info[4][0] for info in infos})


def _pin_to_interface(sock: socket.socket, interface: Optional[str]) -> None:
    """On macOS a broadcast leaves through the default route (often Wi-Fi) unless the socket is
    bound to the ENET interface explicitly. Other systems route by the bound source address."""
    if sys.platform != "darwin" or not interface or interface == "?":
        return
    try:
        sock.setsockopt(socket.IPPROTO_IP, _IP_BOUND_IF, socket.if_nametoindex(interface))
    except OSError:
        pass


def _parse_ident(payload: bytes) -> tuple[Optional[int], Optional[str], str]:
    text = payload.decode("latin1", "replace")

    def field(tag: str, length: int) -> Optional[str]:
        start = text.find(tag)
        return text[start + len(tag):start + len(tag) + length] if start >= 0 else None

    diag = field("DIAGADR", 2)
    vin = field("BMWVIN", 17)
    try:
        diag_address = int(diag, 16) if diag else None
    except ValueError:
        diag_address = None
    return diag_address, (vin.strip("\x00 ") if vin else None), text


class GatewayLocator:
    def __init__(self, ident_port: int = IDENT_PORT, diag_port: int = DIAG_PORT,
                 listen_seconds: float = 3.0, broadcast_attempts: int = 3):
        self._ident_port = ident_port
        self._diag_port = diag_port
        self._listen_seconds = listen_seconds
        self._attempts = broadcast_attempts

    def locate(self, local_ip: str, broadcast_targets: Sequence[str] = _DEFAULT_BROADCASTS,
               interface: Optional[str] = None) -> List[VehicleEndpoint]:
        """Endpoints reachable from ``local_ip``, gateway first; empty if the car is not found."""
        endpoints = self._ident_broadcast(local_ip, broadcast_targets, interface)
        if not endpoints:
            endpoints = self._arp_probe(local_ip)
        return sorted(endpoints, key=lambda e: (not e.is_gateway, e.ip))

    def _ident_broadcast(self, local_ip: str, targets: Sequence[str],
                         interface: Optional[str]) -> List[VehicleEndpoint]:
        found: dict[str, VehicleEndpoint] = {}
        probe = HsfzFrame.ident().encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local_ip, 0))
            _pin_to_interface(sock, interface)
            for _ in range(self._attempts):
                for target in targets:
                    try:
                        sock.sendto(probe, (target, self._ident_port))
                    except OSError:
                        continue
                self._collect_idents(sock, found)
                if found:
                    break
        return list(found.values())

    def _collect_idents(self, sock: socket.socket, found: dict) -> None:
        deadline = time.monotonic() + self._listen_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                datagram, (ip, _) = sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                return
            frame = decode_datagram(datagram)
            if frame is None or frame.control != CTRL_IDENT or not frame.data or ip in found:
                continue
            diag_address, vin, text = _parse_ident(frame.data)
            found[ip] = VehicleEndpoint(ip=ip, port=self._diag_port, diag_address=diag_address,
                                        vin=vin, ident=text, found_by=DiscoveryMethod.IDENT)

    def _arp_probe(self, local_ip: str) -> List[VehicleEndpoint]:
        """Fallback when no ident reply arrived: probe link-local ARP neighbours for TCP 6801."""
        try:
            table = _run_hidden(["arp", "-a"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            return []
        candidates = sorted({ip for ip in _IPV4.findall(table)
                             if ip.startswith(LINK_LOCAL_PREFIX) and ip != local_ip
                             and not ip.endswith(".255")})
        endpoints = []
        for ip in candidates:
            try:
                with socket.create_connection((ip, self._diag_port), timeout=1.0,
                                              source_address=(local_ip, 0)):
                    endpoints.append(VehicleEndpoint(ip=ip, port=self._diag_port, found_by=DiscoveryMethod.ARP))
            except OSError:
                continue
        return endpoints


class VehicleFinder:
    """Waits for the ENET link to get an address, then locates the car's gateway on it."""

    def __init__(self, addresses: Optional[LocalAddressFinder] = None,
                 locator: Optional[GatewayLocator] = None, poll_seconds: float = 3.0):
        self._addresses = addresses or LocalAddressFinder()
        self._locator = locator or GatewayLocator()
        self._poll = poll_seconds

    def find(self, wait_seconds: float, log: Callable[[str], None],
             cancel_requested: threading.Event) -> Optional[VehicleEndpoint]:
        """The best endpoint found within ``wait_seconds``; ``None`` on timeout or cancel."""
        deadline = time.monotonic() + wait_seconds
        announced_wait = False
        while not cancel_requested.is_set():
            for local in self._addresses.find_link_local_addresses():
                log(f"ENET adresi: {local.ip} ({local.interface}) - arac araniyor...")
                endpoints = self._locator.locate(local.ip, interface=local.interface)
                if endpoints:
                    return endpoints[0]
                log(f"{local.ip} uzerinde arac cevap vermedi.")
            if time.monotonic() >= deadline:
                return None
            if not announced_wait:
                log("ENET baglantisi icin 169.254.x.x adresi bekleniyor (Windows'ta 1 dakikayi bulabilir)...")
                announced_wait = True
            cancel_requested.wait(self._poll)
        return None
