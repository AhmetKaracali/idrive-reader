"""A localhost HSFZ gateway (TCP diag port + optional UDP ident) backed by a :class:`VehicleModel`."""
from __future__ import annotations

import socket
import threading
from typing import List, Optional

from ..transport.hsfz import (CTRL_ACK, CTRL_DATA, CTRL_IDENT, TESTER_ADDRESS, HsfzFrame,
                              decode_datagram, take_frame)
from .vehicles import VehicleModel


class SimulatorServer:
    def __init__(self, vehicle: VehicleModel, host: str = "127.0.0.1", diag_port: int = 0,
                 ident_port: Optional[int] = None):
        self._vehicle = vehicle
        self._host = host
        self._requested_diag_port = diag_port
        self._ident_port = ident_port
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._tcp: Optional[socket.socket] = None
        self._udp: Optional[socket.socket] = None
        self._lock = threading.Lock()

    @property
    def diag_port(self) -> int:
        if self._tcp is None:
            raise RuntimeError("server not started")
        return self._tcp.getsockname()[1]

    def start(self) -> "SimulatorServer":
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.bind((self._host, self._requested_diag_port))
        self._tcp.listen()
        self._tcp.settimeout(0.2)
        self._spawn(self._accept_loop)
        if self._ident_port is not None:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.bind((self._host, self._ident_port))
            self._udp.settimeout(0.2)
            self._spawn(self._ident_loop)
        return self

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._tcp, self._udp):
            if sock is not None:
                sock.close()
        for thread in self._threads:
            thread.join(timeout=2)

    def __enter__(self) -> "SimulatorServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _spawn(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._spawn(self._serve, connection)

    def _ident_loop(self) -> None:
        while not self._stop.is_set():
            try:
                datagram, peer = self._udp.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            frame = decode_datagram(datagram)
            if frame is not None and frame.control == CTRL_IDENT:
                self._udp.sendto(HsfzFrame.ident(self._vehicle.ident()).encode(), peer)

    def _serve(self, connection: socket.socket) -> None:
        connection.settimeout(0.2)
        buffer = bytearray()
        with connection:
            while not self._stop.is_set():
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buffer.extend(chunk)
                while (frame := take_frame(buffer)) is not None:
                    self._handle(connection, frame)

    def _handle(self, connection: socket.socket, frame: HsfzFrame) -> None:
        if frame.control != CTRL_DATA or not frame.data:
            return
        with self._lock:
            answers = self._vehicle.respond(frame.target, frame.data)
        outgoing = HsfzFrame(CTRL_ACK, frame.data[:4], frame.source, frame.target).encode()
        for source, response in answers:
            outgoing += HsfzFrame.uds(source, TESTER_ADDRESS, response).encode()
        try:
            connection.sendall(outgoing)
        except OSError:
            return
