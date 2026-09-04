from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from study_builder.util import read_json


@dataclass(frozen=True)
class Book:
    number: int
    name: str
    osis: tuple[str, ...]
    # Verses in each chapter, in chapter order, following the KJV versification the
    # Bible API v3 publishes; empty when the registry does not state them.
    verses: tuple[int, ...] = ()
    # How source text spells this book, per language, used to recognise references
    # written in prose. "1 Sam" and "1 Samuel" carry the ordinal in the spelling.
    names: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def chapter_count(self) -> int | None:
        return len(self.verses) or None

    def verse_count(self, chapter: int) -> int | None:
        if 1 <= chapter <= len(self.verses):
            return self.verses[chapter - 1]
        return None


class BookRegistry:
    def __init__(self, path: Path) -> None:
        records = read_json(path)
        self.books = [
            Book(
                int(item["number"]),
                str(item["name"]),
                tuple(item["osis"]),
                tuple(int(count) for count in item.get("verses", ())),
                {
                    str(language): tuple(str(name) for name in names)
                    for language, names in (item.get("names") or {}).items()
                },
            )
            for item in records
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

    def reference(self, osis_ref: str) -> dict | None:
        parts = osis_ref.split(".")
        book = self.by_osis.get(parts[0].casefold())
        if not book or len(parts) < 2 or not parts[1].isdigit():
            return None
        result = {"osis": osis_ref, "book": book.number, "chapter": int(parts[1])}
        if len(parts) >= 3 and parts[2].isdigit():
            result["verse"] = int(parts[2])
        return result
