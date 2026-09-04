#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from study_builder.util import read_json, slug


def _reject_markup(document: Any, where: str) -> None:
    if isinstance(document, dict):
        if "html" in document:
            raise RuntimeError(f"{where} still publishes an html member")
        for key, value in document.items():
            _reject_markup(value, f"{where}.{key}")
    elif isinstance(document, list):
        for index, value in enumerate(document):
            _reject_markup(value, f"{where}[{index}]")


def _assert_composed(composed: list[Any], parts: list[Path], where: str) -> None:
    """A composed document must contain its parts exactly as they are served alone."""
    if len(composed) != len(parts):
        raise RuntimeError(f"{where} holds {len(composed)} members for {len(parts)} documents")
    for member, path in zip(composed, parts, strict=True):
        if member != read_json(path):
            raise RuntimeError(f"{where} does not match the document served at {path}")


def _assert_references(document: dict[str, Any], where: Path) -> int:
    """Every reference resolves to a coordinate, states its canonical ref, and if it says
    where the text cites it, the text really does."""
    text = str(document.get("text", ""))
    references = document.get("references", [])
    seen: set[tuple[Any, ...]] = set()
    for item in references:
        for name in ("ref", "osis", "book", "chapter"):
            if name not in item:
                raise RuntimeError(f"{where} publishes a reference without {name}")
        if "verses" in item and ("verse" not in item or item["verse"] != min(item["verses"])):
            raise RuntimeError(f"{where} publishes a verse list not anchored at its first verse")
        if "text" in item and item["text"] not in text:
            raise RuntimeError(f"{where} cites {item['text']!r}, which its text does not contain")
        key = (item["book"], item["chapter"], item.get("verse"), tuple(item.get("verses", ())))
        if key in seen:
            raise RuntimeError(f"{where} publishes the same reference twice")
        seen.add(key)
    return len(references)


def _assert_no_repeated_text(chapter: dict[str, Any], where: Path) -> None:
    """The whole point of the collapse: one comment is stored once, not once per verse.

    Two distinct comments may still land on the same verse — a source module can emit
    more than one record for a verse — so verse coverage is deliberately not asserted
    to be disjoint. What must hold is that no text repeats.
    """
    seen: set[str] = set()
    for entry in chapter["entries"]:
        text = entry["text"]
        if text in seen:
            raise RuntimeError(f"{where} publishes the same comment more than once")
        seen.add(text)
        verses = entry.get("verses", [entry["verse"]])
        if entry["verse"] != min(verses):
            raise RuntimeError(f"{where} anchors an entry above the lowest verse it covers")


def validate_commentary(root: Path, complete_path: Path) -> dict[str, Any]:
    metadata = read_json(root / "metadata.json")
    books = read_json(root / "books.json")
    if metadata.get("schema") != "getbible-commentary-metadata-v1":
        raise RuntimeError("Unexpected commentary metadata schema")
    if books.get("schema") != "getbible-commentary-books-v1":
        raise RuntimeError("Unexpected commentary books index schema")
    if int(metadata.get("entry_count", 0)) <= 0 or not books.get("books"):
        raise RuntimeError("Commentary produced no addressable entries")

    first_book = books["books"][0]
    book_path = root / f"{first_book['book']}.json"
    book = read_json(book_path)
    if book.get("schema") != "getbible-commentary-book-v1" or not book.get("chapters"):
        raise RuntimeError("Commentary book document produced no chapters")
    chapter_paths = [
        root / str(first_book["book"]) / f"{number}.json" for number in first_book["chapters"]
    ]
    _assert_composed(book["chapters"], chapter_paths, f"{book_path}.chapters")

    chapter = read_json(chapter_paths[0])
    if chapter.get("schema") != "getbible-commentary-chapter-v1" or not chapter.get("entries"):
        raise RuntimeError("Commentary chapter produced no entries")
    first = chapter["entries"][0]
    if not all(name in first for name in ("book", "chapter", "verse", "text")):
        raise RuntimeError("Commentary entry is not linked to a Bible API coordinate")
    _assert_no_repeated_text(chapter, chapter_paths[0])
    _reject_markup(chapter, "chapter")
    references = sum(_assert_references(item, chapter_paths[0]) for item in chapter["entries"])

    complete = read_json(complete_path)
    if complete.get("schema") != "getbible-commentary-v1":
        raise RuntimeError("Unexpected whole-commentary schema")
    book_paths = [root / f"{record['book']}.json" for record in books["books"]]
    _assert_composed(complete["books"], book_paths, f"{complete_path}.books")

    return {
        "books": metadata["book_count"],
        "chapters": metadata["chapter_count"],
        "entries": metadata["entry_count"],
        "bytes": metadata["bytes"],
        "introductions": sum(1 for record in books["books"] if 0 in record["chapters"]),
        "references_in_first_chapter": references,
        "bible": metadata.get("references"),
    }


def validate_dictionary(root: Path, complete_path: Path) -> dict[str, Any]:
    metadata = read_json(root / "metadata.json")
    index = read_json(root / "index.json")
    if metadata.get("schema") != "getbible-dictionary-metadata-v1":
        raise RuntimeError("Unexpected dictionary metadata schema")
    if index.get("schema") != "getbible-dictionary-index-v1":
        raise RuntimeError("Unexpected dictionary index schema")
    if int(metadata.get("entry_count", 0)) <= 0 or not index.get("entries"):
        raise RuntimeError("Dictionary produced no addressable entries")

    terms = [record["search"] for record in index["entries"]]
    if terms != sorted(terms):
        raise RuntimeError("Dictionary index is not sorted by its search term")

    entry_paths = [root / f"{record['id']}.json" for record in index["entries"]]
    document = read_json(entry_paths[0])
    if document.get("schema") != "getbible-dictionary-entry-v1":
        raise RuntimeError("Unexpected dictionary entry schema")
    if not all(name in document for name in ("dictionary", "id", "key", "aliases", "text")):
        raise RuntimeError("Dictionary entry is missing its lookup contract")
    _reject_markup(document, "entry")

    complete = read_json(complete_path)
    if complete.get("schema") != "getbible-dictionary-v1":
        raise RuntimeError("Unexpected whole-dictionary schema")
    _assert_composed(complete["entries"], entry_paths, f"{complete_path}.entries")

    linked = sum(1 for entry in complete["entries"] if entry.get("see_also"))
    references = sum(_assert_references(entry, complete_path) for entry in complete["entries"])
    cited = sum(1 for entry in complete["entries"] if entry.get("references"))
    unbroken = sum(
        1
        for entry in complete["entries"]
        if len(entry["text"]) > 1500 and "\n" not in entry["text"]
    )
    return {
        "entries": metadata["entry_count"],
        "unique_keys": metadata["unique_key_count"],
        "strong_prefix": metadata["strong_prefix"],
        "bytes": metadata["bytes"],
        "entries_with_links": linked,
        "entries_with_references": cited,
        "references": references,
        "long_entries_without_a_break": unbroken,
        "bible": metadata.get("references"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", choices=("commentaries", "dictionaries"), required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    module_id = slug(args.module)
    version_root = args.dist_dir / args.resource / "v1"
    root = version_root / module_id
    complete_path = version_root / f"{module_id}.json"
    result = (
        validate_commentary(root, complete_path)
        if args.resource == "commentaries"
        else validate_dictionary(root, complete_path)
    )
    print(json.dumps({"resource": args.resource, "module": args.module, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
