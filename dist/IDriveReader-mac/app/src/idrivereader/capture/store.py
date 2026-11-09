"""The output folder of one capture session."""
from __future__ import annotations

import dataclasses
import json
import shutil
from enum import Enum
from pathlib import Path
from typing import IO, Any


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class JsonLinesWriter:
    def __init__(self, path: Path):
        self._file: IO[str] = open(path, "a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._file.write(json.dumps(_to_jsonable(record)) + "\n")

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "JsonLinesWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class CaptureStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def prepare_path(self, name: str) -> Path:
        """Absolute path for ``name`` inside the session folder, creating parent folders."""
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, name: str, payload: Any) -> Path:
        """Write ``payload`` (dataclasses, enums and bytes are converted) as pretty JSON."""
        target = self.prepare_path(name)
        target.write_text(json.dumps(_to_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def write_text(self, name: str, text: str) -> Path:
        target = self.prepare_path(name)
        target.write_text(text, encoding="utf-8")
        return target

    def open_json_lines(self, name: str) -> JsonLinesWriter:
        """Open an append-only JSON-lines file; the caller closes it."""
        return JsonLinesWriter(self.prepare_path(name))

    def rename_folder(self, new_name: str) -> None:
        """Rename the session folder (only once every file in it is closed)."""
        destination = self.root.with_name(new_name)
        if destination != self.root and not destination.exists():
            self.root = self.root.rename(destination)

    def archive(self) -> Path:
        """Zip the session folder next to itself and return the archive path."""
        return Path(shutil.make_archive(str(self.root), "zip", root_dir=self.root))
