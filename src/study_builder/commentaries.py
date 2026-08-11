# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import validate

from study_builder.books import BookRegistry
from study_builder.chapter_spool import CommentaryChapterSpool
from study_builder.content import extract_osis_references, public_content
from study_builder.models import ModuleDescriptor, NativeExport
from study_builder.util import read_json, slug, write_composed_json, write_json

# Reserved inside a commentary directory; an entry may never claim these names.
RESERVED_DOCUMENTS = {"metadata.json", "books.json"}


class CommentaryWriter:
    """Write the chapter, book, and whole-commentary documents for one module.

    Chapter documents are the addressable unit. Book and commentary documents embed
    them byte-for-byte, so `book.chapters[n]` is exactly the chapter document served
    at its own path and a client needs only one parser for all three levels.
    """

    def __init__(self, root: Path, books: BookRegistry, schemas_dir: Path) -> None:
        self.root = root
        self.books = books
        self.chapter_schema = read_json(schemas_dir / "commentary-chapter.schema.json")

    def write(self, module: ModuleDescriptor, exported: NativeExport) -> tuple[dict, dict]:
        module_id = slug(module.name)
        module_root = self.root / module_id
        chapter_files: dict[int, list[Path]] = defaultdict(list)
        chapter_counts: dict[int, list[tuple[int, int]]] = defaultdict(list)
        entry_count = 0

        with CommentaryChapterSpool() as chapters:
            for source in exported.entries:
                entry = self._entry(source)
                if entry is not None:
                    chapters.append(entry)

            for book_number, chapter_number in chapters.coordinates():
                chapter_entries = self._chapter_entries(chapters, book_number, chapter_number)
                if not chapter_entries:
                    continue
                book = self.books.by_number[book_number]
                document = {
                    "schema": "getbible-commentary-chapter-v1",
                    "commentary": module_id,
                    "language": module.language,
                    "book": book_number,
                    "name": book.name,
                    "chapter": chapter_number,
                    "entries": chapter_entries,
                }
                validate(document, self.chapter_schema)
                path = module_root / str(book_number) / f"{chapter_number}.json"
                write_json(path, document)
                chapter_files[book_number].append(path)
                chapter_counts[book_number].append((chapter_number, len(chapter_entries)))
                entry_count += len(chapter_entries)

        book_files: list[Path] = []
        books_index: list[dict[str, Any]] = []
        for book_number in sorted(chapter_files):
            book = self.books.by_number[book_number]
            path = module_root / f"{book_number}.json"
            if path.name in RESERVED_DOCUMENTS:
                raise RuntimeError(f"Book document collides with a reserved name: {path.name}")
            write_composed_json(
                path,
                {
                    "schema": "getbible-commentary-book-v1",
                    "commentary": module_id,
                    "language": module.language,
                    "book": book_number,
                    "name": book.name,
                },
                "chapters",
                chapter_files[book_number],
            )
            book_files.append(path)
            books_index.append(
                {
                    "book": book_number,
                    "name": book.name,
                    "chapters": [number for number, _ in chapter_counts[book_number]],
                    "entry_count": sum(count for _, count in chapter_counts[book_number]),
                }
            )

        write_json(
            module_root / "books.json",
            {
                "schema": "getbible-commentary-books-v1",
                "commentary": module_id,
                "language": module.language,
                "name": module.description,
                "book_url_template": "{book}.json",
                "chapter_url_template": "{book}/{chapter}.json",
                "book_count": len(books_index),
                "books": books_index,
            },
        )

        complete = self.root / f"{module_id}.json"
        write_composed_json(
            complete,
            {
                "schema": "getbible-commentary-v1",
                "commentary": module_id,
                "language": module.language,
                "name": module.description,
            },
            "books",
            book_files,
        )

        chapter_count = sum(len(records) for records in chapter_counts.values())
        metadata = self._metadata(
            module, module_id, len(books_index), chapter_count, entry_count, complete.stat().st_size
        )
        write_json(module_root / "metadata.json", metadata)
        record = {
            "id": module_id,
            "name": module.description,
            "language": module.language,
            "license": module.license,
            "book_count": len(books_index),
            "chapter_count": chapter_count,
            "entry_count": entry_count,
            "bytes": metadata["bytes"],
        }
        return record, metadata

    def _entry(self, source: dict[str, Any]) -> dict[str, Any] | None:
        verse = source.get("verse") or {}
        chapter = int(verse.get("chapter", 0) or 0)
        verse_number = int(verse.get("verse", 0) or 0)
        # Chapter zero carries a book introduction and verse zero a chapter
        # introduction. Both are published: chapter zero at {book}/0.json, and
        # verse zero as the first entry of its chapter.
        if chapter < 0 or verse_number < 0:
            return None
        try:
            book = self.books.from_entry(source)
        except ValueError:
            return None
        content = public_content(source)
        if not content["text"]:
            return None
        label = book.name
        if chapter:
            label += f" {chapter}"
            if verse_number:
                label += f":{verse_number}"
        anchor: dict[str, Any] = {
            "book": book.number,
            "chapter": chapter,
            "verse": verse_number,
        }
        osis = str(verse.get("osis", ""))
        if osis:
            anchor["osis"] = osis
        entry: dict[str, Any] = {
            "book": book.number,
            "chapter": chapter,
            "verse": verse_number,
            "name": label,
            "anchor": anchor,
            **content,
        }
        related = []
        for reference in extract_osis_references(
            str(source.get("raw", "")), str(source.get("html", ""))
        ):
            normalized = self.books.reference(reference)
            if normalized:
                related.append(normalized)
        if related:
            entry["references"] = related
        return entry

    @staticmethod
    def _chapter_entries(
        chapters: CommentaryChapterSpool, book_number: int, chapter_number: int
    ) -> list[dict[str, Any]]:
        seen: set[tuple[int, str]] = set()
        collected: list[dict[str, Any]] = []
        for entry in chapters.entries(book_number, chapter_number):
            unique = (int(entry["verse"]), str(entry.get("text", "")))
            if unique in seen:
                continue
            seen.add(unique)
            collected.append(entry)
        collected.sort(key=lambda item: (item["verse"], item["name"]))
        return collected

    @staticmethod
    def _metadata(
        module: ModuleDescriptor,
        module_id: str,
        book_count: int,
        chapter_count: int,
        entry_count: int,
        complete_bytes: int,
    ) -> dict[str, Any]:
        return {
            "schema": "getbible-commentary-metadata-v1",
            "id": module_id,
            "module": module.name,
            "name": module.description,
            "language": module.language,
            "version": module.version,
            "license": module.license,
            "driver": module.driver,
            "source_type": module.first("sourcetype"),
            "versification": module.first("versification", "KJV"),
            "book_count": book_count,
            "chapter_count": chapter_count,
            "entry_count": entry_count,
            "bytes": complete_bytes,
            "books_url": "books.json",
            "book_url_template": "{book}.json",
            "chapter_url_template": "{book}/{chapter}.json",
            "source": "CrossWire SWORD",
            "source_module_url": (
                "https://www.crosswire.org/sword/modules/ModInfo.jsp?modName="
                + quote(module.name, safe="")
            ),
            "text_source": module.first("textsource"),
            "copyright": module.first("copyright"),
            "copyright_holder": module.first("copyrightholder"),
            "copyright_contact": {
                "name": module.first("copyrightcontactname"),
                "email": module.first("copyrightcontactemail"),
                "address": module.first("copyrightcontactaddress"),
            },
            "distribution_notes": module.first("distributionnotes"),
            "about": module.first("about"),
            "conversion_note": (
                "Converted to GetBible static JSON; wording is supplied by the source module."
            ),
        }
