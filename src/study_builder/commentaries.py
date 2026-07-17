from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import validate

from study_builder.books import BookRegistry
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
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        seen: set[tuple[int, int, int, str]] = set()
        for source in exported.entries:
            verse = source.get("verse") or {}
            chapter = int(verse.get("chapter", 0) or 0)
            verse_number = int(verse.get("verse", 0) or 0)
            if chapter < 0 or verse_number < 0:
                continue
            try:
                book = self.books.from_entry(source)
            except ValueError:
                continue
            content = public_content(source)
            if not content.get("text") and not content.get("html"):
                continue
            unique = (book.number, chapter, verse_number, content.get("text", ""))
            if unique in seen:
                continue
            seen.add(unique)
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
            grouped[(book.number, chapter)].append(entry)

        books_index: list[dict[str, Any]] = []
        for book_number in sorted({key[0] for key in grouped}):
            book = self.books.by_number[book_number]
            chapter_numbers = sorted(chapter for nr, chapter in grouped if nr == book_number)
            chapter_index: list[dict[str, Any]] = []
            for chapter_number in chapter_numbers:
                entries = sorted(
                    grouped[(book_number, chapter_number)],
                    key=lambda item: (item["verse"], item["name"]),
                )
                document = {
                    "schema": "getbible-commentary-chapter-v1",
                    "commentary": module_id,
                    "language": module.language,
                    "book": book_number,
                    "name": book.name,
                    "chapter": chapter_number,
                    "entries": entries,
                }
                validate(document, self.schema)
                write_json(module_root / str(book_number) / f"{chapter_number}.json", document)
                chapter_index.append(
                    {
                        "chapter": chapter_number,
                        "entry_count": len(entries),
                        "url": f"{book_number}/{chapter_number}.json",
                    }
                )
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
            "entry_count": sum(len(entries) for entries in grouped.values()),
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
