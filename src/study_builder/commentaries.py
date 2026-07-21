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
from study_builder.util import read_json, slug, write_json


class CommentaryWriter:
    def __init__(self, root: Path, books: BookRegistry, schema_path: Path) -> None:
        self.root = root
        self.books = books
        self.schema = read_json(schema_path)

    def write(self, module: ModuleDescriptor, exported: NativeExport) -> dict[str, Any]:
        module_id = slug(module.name)
        module_root = self.root / module_id
        chapter_indexes: dict[int, list[dict[str, Any]]] = defaultdict(list)
        entry_count = 0

        with CommentaryChapterSpool() as chapters:
            for source in exported.entries:
                verse = source.get("verse") or {}
                chapter = int(verse.get("chapter", 0) or 0)
                verse_number = int(verse.get("verse", 0) or 0)
                # The public commentary API is chapter-addressable. SWORD modules may
                # also expose book introductions with chapter zero; those records do
                # not have a chapter endpoint and intentionally remain unpublished.
                if chapter <= 0 or verse_number < 0:
                    continue
                try:
                    book = self.books.from_entry(source)
                except ValueError:
                    continue
                content = public_content(source)
                if not content.get("text") and not content.get("html"):
                    continue
                osis = str(verse.get("osis", ""))
                related = []
                for reference in extract_osis_references(
                    str(source.get("raw", "")), str(source.get("html", ""))
                ):
                    normalized = self.books.reference(reference)
                    if normalized:
                        related.append(normalized)
                anchor = {
                    "book": book.number,
                    "chapter": chapter,
                    "verse": verse_number,
                }
                if osis:
                    anchor["osis"] = osis
                label = book.name
                if chapter:
                    label += f" {chapter}"
                    if verse_number:
                        label += f":{verse_number}"
                entry: dict[str, Any] = {
                    "book": book.number,
                    "chapter": chapter,
                    "verse": verse_number,
                    "name": label,
                    "anchor": anchor,
                    **content,
                }
                if related:
                    entry["references"] = related
                chapters.append(entry)

            for book_number, chapter_number in chapters.coordinates():
                chapter_seen: set[tuple[int, str]] = set()
                chapter_entries: list[dict[str, Any]] = []
                for entry in chapters.entries(book_number, chapter_number):
                    unique = (int(entry["verse"]), str(entry.get("text", "")))
                    if unique in chapter_seen:
                        continue
                    chapter_seen.add(unique)
                    chapter_entries.append(entry)
                chapter_entries.sort(key=lambda item: (item["verse"], item["name"]))
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
                validate(document, self.schema)
                write_json(module_root / str(book_number) / f"{chapter_number}.json", document)
                chapter_indexes[book_number].append(
                    {
                        "chapter": chapter_number,
                        "entry_count": len(chapter_entries),
                        "url": f"{book_number}/{chapter_number}.json",
                    }
                )
                entry_count += len(chapter_entries)

        books_index: list[dict[str, Any]] = []
        for book_number in sorted(chapter_indexes):
            book = self.books.by_number[book_number]
            chapter_index = chapter_indexes[book_number]
            chapter_numbers = [record["chapter"] for record in chapter_index]
            write_json(
                module_root / f"{book_number}.json",
                {
                    "schema": "getbible-commentary-book-index-v1",
                    "commentary": module_id,
                    "language": module.language,
                    "book": book_number,
                    "name": book.name,
                    "chapters": chapter_index,
                },
            )
            books_index.append(
                {
                    "book": book_number,
                    "name": book.name,
                    "chapters": chapter_numbers,
                    "url": f"{book_number}.json",
                    "chapter_url_template": f"{book_number}/{{chapter}}.json",
                }
            )

        metadata = {
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
            "entry_count": entry_count,
            "book_count": len(books_index),
            "books_url": "books.json",
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
        write_json(module_root / "metadata.json", metadata)
        write_json(module_root / "books.json", books_index)
        return metadata
