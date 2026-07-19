# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import gzip
import json
import tempfile
from collections.abc import Iterator, Sequence
from typing import Any, overload


class EntrySpool(Sequence[dict[str, Any]]):
    """A repeatable, compressed, disk-backed sequence of validated entries."""

    def __init__(self) -> None:
        self._file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - export lifetime
        self._writer = gzip.GzipFile(
            fileobj=self._file,
            mode="wb",
            compresslevel=1,
            mtime=0,
        )
        self._count = 0
        self._finished = False
        self._closed = False

    def append(self, entry: dict[str, Any]) -> None:
        if self._finished or self._closed:
            raise RuntimeError("Cannot append to a finished entry spool")
        payload = json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._writer.write(payload + b"\n")
        self._count += 1

    def finish(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot finish a closed entry spool")
        if not self._finished:
            self._writer.close()
            self._file.seek(0)
            self._finished = True

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self._finished or self._closed:
            raise RuntimeError("Entry spool is not available for reading")
        self._file.seek(0)
        with gzip.GzipFile(fileobj=self._file, mode="rb") as reader:
            for line in reader:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise RuntimeError("Entry spool contains a non-object record")
                yield entry

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            return list(self)[index]
        normalized = index if index >= 0 else self._count + index
        if normalized < 0 or normalized >= self._count:
            raise IndexError("entry spool index out of range")
        for offset, entry in enumerate(self):
            if offset == normalized:
                return entry
        raise IndexError("entry spool index out of range")

    def close(self) -> None:
        if getattr(self, "_closed", True):
            return
        if not self._finished:
            self._writer.close()
        self._file.close()
        self._closed = True

    def __del__(self) -> None:
        self.close()
