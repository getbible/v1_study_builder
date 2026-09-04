# SPDX-License-Identifier: GPL-2.0-only
"""The GetBible book numbering: number, English name, and OSIS identifiers per book.

This is identity data — which number a book has and how OSIS spells it — and nothing
about its shape. How many chapters and verses a book has, and what a language calls it,
are read from the Bible API by :mod:`study_builder.bible`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from study_builder.util import read_json


@dataclass(frozen=True)
class Book:
    number: int
    name: str
    osis: tuple[str, ...]


class BookRegistry:
    def __init__(self, path: Path) -> None:
        records = read_json(path)
        self.books = [
            Book(int(item["number"]), str(item["name"]), tuple(item["osis"])) for item in records
        ]
        self.by_number = {book.number: book for book in self.books}
        self.by_osis = {alias.casefold(): book for book in self.books for alias in book.osis}

    def from_entry(self, entry: dict) -> Book:
        verse = entry.get("verse") or {}
        osis_ref = str(verse.get("osis", ""))
        if osis_ref:
            book = self.by_osis.get(osis_ref.split(".", 1)[0].casefold())
            if book:
                return book
        testament = int(verse.get("testament", 0) or 0)
        ordinal = int(verse.get("book", 0) or 0)
        if testament == 1 and 1 <= ordinal <= 39:
            return self.by_number[ordinal]
        if testament == 2 and 1 <= ordinal <= 27:
            return self.by_number[39 + ordinal]
        raise ValueError(f"Unable to map commentary key to a GetBible book: {entry.get('key')!r}")
