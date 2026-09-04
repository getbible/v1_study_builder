# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import validate

from study_builder.books import BookRegistry
from study_builder.chapter_spool import CommentaryChapterSpool
from study_builder.content import extract_markup_references, public_content
from study_builder.dictionaries import source_type
from study_builder.models import ModuleDescriptor, NativeExport
from study_builder.references import ReferenceEngine
from study_builder.util import (
    DOCUMENT_CEILING_BYTES,
    enforce_document_ceiling,
    read_json,
    slug,
    write_composed_json,
    write_json,
)

# Reserved inside a commentary directory; an entry may never claim these names.
RESERVED_DOCUMENTS = {"metadata.json", "books.json"}


class CommentaryWriter:
    """Write the chapter, book, and whole-commentary documents for one module.

    Chapter documents are the addressable unit. Book and commentary documents embed
    them byte-for-byte, so `book.chapters[n]` is exactly the chapter document served
    at its own path and a client needs only one parser for all three levels.
    """

    def __init__(
        self,
        root: Path,
        books: BookRegistry,
        schemas_dir: Path,
        max_document_bytes: int = DOCUMENT_CEILING_BYTES,
        *,
        references: ReferenceEngine,
    ) -> None:
        self.root = root
        self.books = books
        self.max_document_bytes = max_document_bytes
        self.chapter_schema = read_json(schemas_dir / "commentary-chapter.schema.json")
        self.references = references
        self.source_type = ""

    def write(self, module: ModuleDescriptor, exported: NativeExport) -> tuple[dict, dict]:
        module_id = slug(module.name)
        module_root = self.root / module_id
        self.source_type = source_type(module, exported.metadata)
        chapter_files: dict[int, list[Path]] = defaultdict(list)
        chapter_counts: dict[int, list[tuple[int, int]]] = defaultdict(list)
        entry_count = 0
        # Measured, not assumed: what the source offered against what is published.
        source_entry_count = 0
        source_text_bytes = 0
        text_bytes = 0
        chapter_bytes = 0
        book_bytes = 0

        with CommentaryChapterSpool() as chapters:
            for source in exported.entries:
                entry = self._entry(source)
                if entry is not None:
                    source_entry_count += 1
                    source_text_bytes += len(entry["text"].encode("utf-8"))
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
                chapter_bytes += enforce_document_ceiling(path, self.max_document_bytes)
                chapter_files[book_number].append(path)
                chapter_counts[book_number].append((chapter_number, len(chapter_entries)))
                entry_count += len(chapter_entries)
                text_bytes += sum(len(item["text"].encode("utf-8")) for item in chapter_entries)

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
            book_bytes += enforce_document_ceiling(path, self.max_document_bytes)
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
        complete_bytes = enforce_document_ceiling(complete, self.max_document_bytes)

        chapter_count = sum(len(records) for records in chapter_counts.values())
        storage = {
            "source_entry_count": source_entry_count,
            "source_text_bytes": source_text_bytes,
            "text_bytes": text_bytes,
            "repetition_ratio": round(source_text_bytes / text_bytes, 3) if text_bytes else 1.0,
            "chapter_bytes": chapter_bytes,
            "book_bytes": book_bytes,
            "commentary_bytes": complete_bytes,
            "published_bytes": chapter_bytes + book_bytes + complete_bytes,
        }
        metadata = self._metadata(
            module, module_id, len(books_index), chapter_count, entry_count, storage
        )
        metadata["references"] = self.references.describe()
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
        content = public_content(source, source_type=self.source_type)
        if not content["text"]:
            return None
        entry: dict[str, Any] = {
            "book": book.number,
            "chapter": chapter,
            "verse": verse_number,
        }
        osis = str(verse.get("osis", ""))
        if osis:
            entry["osis"] = osis
        entry.update(content)
        # What the markup cites is authoritative for the text it covers; the rest is
        # read as prose. A comment cites its own book when it names none, so that book
        # and chapter seed what an unqualified "3:16" or "ver. 5" resolves to.
        related = self.references.extract(
            content["text"],
            markup=extract_markup_references(
                str(source.get("raw", "")), str(source.get("html", ""))
            ),
            book=book.number,
            chapter=chapter if chapter > 0 else None,
        )
        if related:
            entry["references"] = related
        return entry

    @staticmethod
    def _chapter_entries(
        chapters: CommentaryChapterSpool, book_number: int, chapter_number: int
    ) -> list[dict[str, Any]]:
        """Publish each distinct comment once, listing every verse it covers.

        A SWORD commentary attaches one comment to a verse *range* and the extractor
        reports that same text once per verse in the range, so writing an entry per
        verse stored the identical paragraph dozens of times — Augustine's exposition
        of a psalm reappeared for all 176 verses of Psalm 119. Nothing is dropped:
        the text is published once, anchored at the lowest verse it covers, and
        `verses` names the rest, so every verse still resolves to its comment.

        Grouping stops at the chapter boundary. A chapter document is the addressable
        unit and has to stand alone, so a comment spanning two chapters is published
        in each of them rather than only in the first.
        """
        order: list[str] = []
        anchors: dict[str, dict[str, Any]] = {}
        covered: dict[str, set[int]] = {}
        references: dict[str, dict[str, dict[str, Any]]] = {}
        for entry in chapters.entries(book_number, chapter_number):
            text = str(entry.get("text", ""))
            verse = int(entry["verse"])
            if text not in anchors:
                order.append(text)
                anchors[text] = entry
                covered[text] = set()
                references[text] = {}
            elif verse < int(anchors[text]["verse"]):
                # The lowest verse anchors the published entry, so its `osis` is the
                # one the source module keyed the comment on.
                anchors[text] = entry
            covered[text].add(verse)
            # A repeated comment repeats its references; union them so a range that
            # does differ verse to verse keeps every reference it carried. Insertion
            # order is kept because references recognised in prose are published in
            # the order the text cites them.
            for reference in entry.get("references", ()):
                references[text].setdefault(json.dumps(reference, sort_keys=True), reference)

        collected: list[dict[str, Any]] = []
        for text in order:
            anchor = anchors[text]
            verses = sorted(covered[text])
            published: dict[str, Any] = {
                "book": int(anchor["book"]),
                "chapter": int(anchor["chapter"]),
                "verse": verses[0],
            }
            if len(verses) > 1:
                published["verses"] = verses
            if anchor.get("osis"):
                published["osis"] = str(anchor["osis"])
            published["text"] = text
            related = list(references[text].values())
            if related:
                published["references"] = related
            collected.append(published)
        collected.sort(key=lambda item: (item["verse"], item["text"]))
        return collected

    @staticmethod
    def _metadata(
        module: ModuleDescriptor,
        module_id: str,
        book_count: int,
        chapter_count: int,
        entry_count: int,
        storage: dict[str, Any],
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
            "bytes": storage["commentary_bytes"],
            "storage": storage,
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
