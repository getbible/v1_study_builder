# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class CommentaryChapterSpool:
    """Disk-backed staging for commentary entries grouped by canonical chapter."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="study-builder-commentaries-")
        database = Path(self._directory.name) / "chapters.sqlite3"
        self._connection = sqlite3.connect(database)
        self._connection.execute("PRAGMA journal_mode = OFF")
        self._connection.execute("PRAGMA synchronous = OFF")
        self._connection.execute("PRAGMA temp_store = FILE")
        self._connection.execute(
            """
            CREATE TABLE entries (
                sequence INTEGER PRIMARY KEY,
                book INTEGER NOT NULL,
                chapter INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX entries_by_chapter ON entries (book, chapter, sequence)"
        )
        self._sequence = 0
        self._closed = False

    def append(self, entry: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("Cannot append to a closed commentary chapter spool")
        payload = json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
        )
        self._connection.execute(
            "INSERT INTO entries (sequence, book, chapter, payload) VALUES (?, ?, ?, ?)",
            (self._sequence, int(entry["book"]), int(entry["chapter"]), payload),
        )
        self._sequence += 1

    def coordinates(self) -> Iterator[tuple[int, int]]:
        if self._closed:
            raise RuntimeError("Commentary chapter spool is closed")
        cursor = self._connection.execute(
            "SELECT book, chapter FROM entries GROUP BY book, chapter ORDER BY book, chapter"
        )
        for book, chapter in cursor:
            yield int(book), int(chapter)

    def entries(self, book: int, chapter: int) -> Iterator[dict[str, Any]]:
        if self._closed:
            raise RuntimeError("Commentary chapter spool is closed")
        cursor = self._connection.execute(
            "SELECT payload FROM entries WHERE book = ? AND chapter = ? ORDER BY sequence",
            (book, chapter),
        )
        for (payload,) in cursor:
            entry = json.loads(payload)
            if not isinstance(entry, dict):
                raise RuntimeError("Commentary chapter spool contains a non-object entry")
            yield entry

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._directory.cleanup()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
