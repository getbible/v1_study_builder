#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from study_builder.util import read_json, slug


def validate_commentary(root: Path) -> dict[str, Any]:
    metadata = read_json(root / "metadata.json")
    books = read_json(root / "books.json")
    if metadata.get("schema") != "getbible-commentary-metadata-v1":
        raise RuntimeError("Unexpected commentary metadata schema")
    if int(metadata.get("entry_count", 0)) <= 0 or not books:
        raise RuntimeError("Commentary produced no addressable entries")
    first_book = books[0]
    book_index = read_json(root / str(first_book["url"]))
    if not book_index.get("chapters"):
        raise RuntimeError("Commentary book index produced no chapters")
    chapter = read_json(root / str(book_index["chapters"][0]["url"]))
    if chapter.get("schema") != "getbible-commentary-chapter-v1":
        raise RuntimeError("Unexpected commentary chapter schema")
    if not chapter.get("entries"):
        raise RuntimeError("Commentary chapter produced no entries")
    first = chapter["entries"][0]
    if not all(name in first for name in ("book", "chapter", "verse", "anchor", "text")):
        raise RuntimeError("Commentary entry is not linked to a Bible API coordinate")
    return {"entries": metadata["entry_count"], "books": metadata["book_count"]}


def validate_dictionary(root: Path) -> dict[str, Any]:
    metadata = read_json(root / "metadata.json")
    keys = read_json(root / "keys.json")
    if metadata.get("schema") != "getbible-dictionary-metadata-v1":
        raise RuntimeError("Unexpected dictionary metadata schema")
    if int(metadata.get("entry_count", 0)) <= 0 or not keys:
        raise RuntimeError("Dictionary produced no addressable entries")
    first = keys[0]
    document = read_json(root / str(first["url"]))
    if document.get("schema") != "getbible-dictionary-entry-v1":
        raise RuntimeError("Unexpected dictionary entry schema")
    if not all(name in document for name in ("dictionary", "id", "key", "aliases", "text")):
        raise RuntimeError("Dictionary entry is missing its lookup contract")
    return {"entries": metadata["entry_count"], "strong_prefix": metadata["strong_prefix"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", choices=("commentaries", "dictionaries"), required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = args.dist_dir / args.resource / "v1" / slug(args.module)
    result = (
        validate_commentary(root) if args.resource == "commentaries" else validate_dictionary(root)
    )
    print(json.dumps({"resource": args.resource, "module": args.module, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
