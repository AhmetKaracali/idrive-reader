"""
The ``frames.jsonl`` format: every HSFZ frame of a session as one JSON line. Writer and reader live
together here so a format change cannot break replay of captures silently.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator, Optional

from .hsfz import HsfzFrame

FRAMES_FILE = "frames.jsonl"


@dataclass(frozen=True)
class RecordedFrame:
    timestamp: float
    direction: str  # "tx" (laptop -> car) or "rx" (car -> laptop)
    control: int
    source: Optional[int]
    target: Optional[int]
    data: bytes


class FrameRecorder:
    """A :class:`~idrivereader.transport.hsfz.FrameObserver` that appends frames to a file."""

    def __init__(self, path: Path, flush_interval: float = 1.0):
        self._file: IO[str] = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._last_flush = time.monotonic()
        self.frame_count = 0

    def on_frame(self, direction: str, frame: HsfzFrame, timestamp: float) -> None:
        record = {"t": round(timestamp, 6), "dir": direction, "ctrl": frame.control,
                  "src": frame.source, "tgt": frame.target, "data": frame.data.hex()}
        with self._lock:
            if self._file.closed:
                return
            self._file.write(json.dumps(record) + "\n")
            self.frame_count += 1
            if time.monotonic() - self._last_flush >= self._flush_interval:
                self._file.flush()
                self._last_flush = time.monotonic()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()


def read_frames(path: Path) -> Iterator[RecordedFrame]:
    """Yield the frames of a ``frames.jsonl`` written by :class:`FrameRecorder`, in order."""
    with open(path, encoding="utf-8") as frames:
        for line in frames:
            if not line.strip():
                continue
            record = json.loads(line)
            yield RecordedFrame(record["t"], record["dir"], record["ctrl"], record["src"],
                                record["tgt"], bytes.fromhex(record["data"]))
