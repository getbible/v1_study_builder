# SPDX-License-Identifier: GPL-2.0-only
"""Describe one generated ``v1`` tree as an OpenAPI document.

The builder writes static files and knows nothing about where they are served.
``openapi.json`` at each version root tells whoever serves the tree, and whoever
consumes it, what every path holds: the documents, their parameters, the JSON
Schema of each, and the rules for reading them. Paths start at ``/v1`` and no
server is named, so the document is true wherever the tree is mounted at a
server's root; a deployment under a prefix adds a ``servers`` entry of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from study_builder.models import ResourceKind
from study_builder.util import read_json

OPENAPI_VERSION = "3.1.0"

# The schemas each tree publishes under schema/, in the order the description
# introduces them. Every one is embedded under components, so the document stands
# alone, and every one is also served beside the data.
SCHEMA_NAMES: dict[str, tuple[str, ...]] = {
    "commentaries": (
        "commentary-catalog",
        "commentary-metadata",
        "commentary-books",
        "commentary-chapter",
        "commentary-book",
        "commentary",
        "build",
        "hashes",
    ),
    "dictionaries": (
        "dictionary-catalog",
        "dictionary-metadata",
        "dictionary-index",
        "dictionary-entry",
        "dictionary",
        "build",
        "hashes",
    ),
}

_TITLES = {"commentaries": "GetBible Commentaries v1", "dictionaries": "GetBible Dictionaries v1"}

_COMMON = """\
Every path below is a file in a static tree; there is no query string, no content
negotiation and no request-time logic, and every document is plain-text JSON that
carries no HTML.

**Where the paths start.** Paths are given from the server root with `v1` as the
first segment, which is how the tree is laid out on disk. No host is assumed. A tree
mounted under a prefix is described by adding a `servers` entry with that prefix.

**Integrity.** `hashes.json` holds the SHA-256 of every other document in the tree,
this one included, and is therefore the complete list of paths the tree contains.
`build.json` records which builder and extractor produced the tree, when, and which
Bible API its scripture references were resolved against. `schema/` holds the JSON
Schema of every document type; the same schemas are embedded under `components` here.
A document is byte-stable between builds while its module is unchanged; only the
catalog and `build.json` carry the build time.

**Scripture references.** `references` on an entry are the passages it cites,
resolved to GetBible coordinates: `book`, `chapter` and `verse`, with `verses` when
more than one verse is covered and no `verse` at all for a whole chapter. `osis` is
the OSIS identifier of the first verse. `ref` is the citation written canonically, in
the book names of the module's language, in the form the GetBible Query API accepts.
`text` is the citation exactly as the entry's own text spells it, present whenever the
citation was located there, so a client can link it in place.
"""

_DESCRIPTIONS = {
    "commentaries": """\
Static JSON documents converted from CrossWire SWORD commentary modules by the
GetBible study builder.

**Finding a commentary.** `commentaries.json` lists every commentary with its id,
language, licence, counts and the byte size of its whole-commentary document, and
gives the path template of every other document relative to `v1/`. The id is the
first path segment of all of that commentary's documents.

**Addressing.** `book` is the GetBible book number: Genesis is 1, Matthew 40,
Revelation 66, and the deuterocanonical books continue to 83. `chapter` is the
chapter number; chapter 0 is the book introduction, and verse 0 within a chapter is
the chapter introduction. `books.json` lists the books and chapters a commentary
actually has, so nothing need be guessed.

**Three self-similar levels.** A chapter document is one member of a book document,
which is one member of the whole-commentary document, each embedded byte-for-byte, so
one parser reads all three. `metadata.json` gives the size of the whole document
before it is requested.

**Reading an entry.** A comment that covers a verse range is published once, anchored
at the lowest verse, with `verses` listing every verse it covers; an entry without
`verses` covers `verse` alone. The comment for verse *n* of a chapter is therefore
`entries.find(e => (e.verses ?? [e.verse]).includes(n))`. A comment spanning a
chapter break appears in both chapters, because each chapter document stands alone.

"""
    + _COMMON,
    "dictionaries": """\
Static JSON documents converted from CrossWire SWORD dictionary and lexicon modules
by the GetBible study builder.

**Finding a dictionary.** `dictionaries.json` lists every dictionary with its id,
language, licence, counts, its Strong's prefix when it is a Strong's lexicon, and the
byte size of its whole-dictionary document, and gives the path template of every
other document relative to `v1/`. The id is the first path segment of all of that
dictionary's documents.

**Finding a word.** `index.json` lists every word once, sorted by `search`, the
accent-insensitive lowercase form of the key, so one fetch is enough to search,
prefix-match or binary-search a dictionary in memory. Each record's `id` is the
document name of the word itself, `{id}.json`, so a hit resolves with one further
request and no other lookup. Strong's lexicons use the Bible API's own tokens as ids:
`G3056` for Greek, `H0430` for Hebrew (`H0` plus the unpadded number). Other keys
receive deterministic path-safe ids.

**Repeated keys.** A dictionary may define the same key more than once. The first
definition keeps the plain id and later ones carry `--2`, `--3` and so on; every
definition is in the index, and `occurrence` states its position among the
definitions of that key.

**Links.** `see_also` lists the words an entry points at and `backlinks` the words
that point back, each as an id and key in the same dictionary, so a client navigates
in either direction without building an index. The whole-dictionary document holds
every entry in index order, byte-for-byte as served alone, for clients that would
otherwise request each word.

"""
    + _COMMON,
}

_TAGS = {
    "commentaries": [
        {"name": "tree", "description": "Documents that describe the whole tree."},
        {"name": "commentary", "description": "The documents of one commentary."},
    ],
    "dictionaries": [
        {"name": "tree", "description": "Documents that describe the whole tree."},
        {"name": "dictionary", "description": "The documents of one dictionary."},
    ],
}

_BOOK_PARAMETER = {
    "name": "book",
    "in": "path",
    "required": True,
    "description": (
        "GetBible book number: Genesis is 1, Matthew 40, Revelation 66, and the "
        "deuterocanonical books continue to 83. books.json lists the numbers the "
        "commentary has."
    ),
    "schema": {"type": "integer", "minimum": 1, "maximum": 83},
}
_CHAPTER_PARAMETER = {
    "name": "chapter",
    "in": "path",
    "required": True,
    "description": (
        "Chapter number; 0 is the book introduction. books.json lists the chapters each book has."
    ),
    "schema": {"type": "integer", "minimum": 0},
}
_ENTRY_PARAMETER = {
    "name": "entry",
    "in": "path",
    "required": True,
    "description": (
        "The word's id as index.json lists it: a Strong's token such as G3056 or H0430 "
        "in a Strong's lexicon, otherwise a deterministic path-safe id, with --2, --3 "
        "and so on for later definitions of a repeated key."
    ),
    "schema": {"type": "string", "minLength": 1},
}
_NOT_FOUND = {
    "description": (
        "No such document. The tree is static: a path that hashes.json does not list "
        "does not exist."
    )
}


def openapi_document(
    kind: ResourceKind, module_ids: list[str], schemas_dir: Path, api_version: int = 1
) -> dict[str, Any]:
    """The OpenAPI description of one tree, from its build and the published schemas."""
    names = SCHEMA_NAMES[kind]
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": _TITLES[kind],
            "version": str(api_version),
            "summary": (
                f"The GetBible {kind} as static JSON: every path is a file, every "
                "document is plain text."
            ),
            "description": _DESCRIPTIONS[kind],
        },
        "tags": _TAGS[kind],
        "paths": {
            **_tree_paths(kind, names),
            **(
                _commentary_paths(module_ids)
                if kind == "commentaries"
                else _dictionary_paths(module_ids)
            ),
        },
        "components": {"schemas": _component_schemas(schemas_dir, names)},
    }


def _operation(
    operation_id: str,
    tag: str,
    summary: str,
    description: str,
    schema: dict[str, Any],
    parameters: list[dict[str, Any]] = (),
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "description": description,
        "operationId": operation_id,
    }
    if parameters:
        operation["parameters"] = list(parameters)
    operation["responses"] = {
        "200": {"description": summary, "content": {"application/json": {"schema": schema}}},
        "404": _NOT_FOUND,
    }
    return {"get": operation}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _module_parameter(name: str, module_ids: list[str], catalog: str) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "minLength": 1}
    if module_ids:
        schema["enum"] = sorted(module_ids)
    return {
        "name": name,
        "in": "path",
        "required": True,
        "description": f"The {name}'s id, as {catalog} lists it.",
        "schema": schema,
    }


def _tree_paths(kind: ResourceKind, names: tuple[str, ...]) -> dict[str, Any]:
    prefix = "commentary" if kind == "commentaries" else "dictionary"
    singular = prefix
    return {
        f"/v1/{kind}.json": _operation(
            f"list{kind.capitalize()}",
            "tree",
            f"The catalog of every {singular} in the tree",
            f"Every {singular} this tree holds, with its counts and sizes, and the path "
            "template of each of its documents relative to v1/.",
            _ref(f"{prefix}-catalog"),
        ),
        "/v1/build.json": _operation(
            "getBuild",
            "tree",
            "How and when the tree was built",
            "The builder and extractor versions, the build time, the module catalog "
            "the modules were selected from and the Bible API the references were "
            "resolved against.",
            _ref("build"),
        ),
        "/v1/hashes.json": _operation(
            "getHashes",
            "tree",
            "The SHA-256 of every other document",
            "The integrity manifest of the tree: a digest for every other document, "
            "which is also the complete list of the paths the tree contains.",
            _ref("hashes"),
        ),
        "/v1/openapi.json": _operation(
            "getOpenApi",
            "tree",
            "This description",
            "The OpenAPI description of the tree, generated with it.",
            {"type": "object"},
        ),
        "/v1/schema/{document}.json": _operation(
            "getSchema",
            "tree",
            "The JSON Schema of one document type",
            "The schemas embedded under components, served beside the data.",
            {"type": "object"},
            [
                {
                    "name": "document",
                    "in": "path",
                    "required": True,
                    "description": "The document type.",
                    "schema": {"type": "string", "enum": list(names)},
                }
            ],
        ),
    }


def _commentary_paths(module_ids: list[str]) -> dict[str, Any]:
    module = _module_parameter("commentary", module_ids, "commentaries.json")
    return {
        "/v1/{commentary}/metadata.json": _operation(
            "getCommentaryMetadata",
            "commentary",
            "One commentary's provenance, licence, counts and sizes",
            "Where the module came from, under which licence, how much it holds, how "
            "large its whole-commentary document is, and which Bible its references "
            "were resolved against.",
            _ref("commentary-metadata"),
            [module],
        ),
        "/v1/{commentary}/books.json": _operation(
            "getCommentaryBooks",
            "commentary",
            "The books and chapters one commentary covers",
            "Every book the commentary comments on, with the chapters it has and the "
            "number of entries in each book. Chapter 0 is a book introduction.",
            _ref("commentary-books"),
            [module],
        ),
        "/v1/{commentary}/{book}/{chapter}.json": _operation(
            "getCommentaryChapter",
            "commentary",
            "One chapter of one commentary",
            "Every comment on one chapter, each published once and anchored at the "
            "lowest verse it covers. Verse 0 is the chapter introduction.",
            _ref("commentary-chapter"),
            [module, _BOOK_PARAMETER, _CHAPTER_PARAMETER],
        ),
        "/v1/{commentary}/{book}.json": _operation(
            "getCommentaryBook",
            "commentary",
            "Every chapter of one book",
            "The chapter documents of one book, each byte-for-byte the document served "
            "at its own path.",
            _ref("commentary-book"),
            [module, _BOOK_PARAMETER],
        ),
        "/v1/{commentary}.json": _operation(
            "getCommentary",
            "commentary",
            "The whole commentary",
            "Every book document of the commentary, each byte-for-byte the document "
            "served at its own path. A bulk document: metadata.json states its size.",
            _ref("commentary"),
            [module],
        ),
    }


def _dictionary_paths(module_ids: list[str]) -> dict[str, Any]:
    module = _module_parameter("dictionary", module_ids, "dictionaries.json")
    return {
        "/v1/{dictionary}/metadata.json": _operation(
            "getDictionaryMetadata",
            "dictionary",
            "One dictionary's provenance, licence, counts and sizes",
            "Where the module came from, under which licence, how many words it holds, "
            "how large its whole-dictionary document is, and which Bible its "
            "references were resolved against.",
            _ref("dictionary-metadata"),
            [module],
        ),
        "/v1/{dictionary}/index.json": _operation(
            "getDictionaryIndex",
            "dictionary",
            "Every word of one dictionary",
            "Every word once, sorted by its accent-insensitive lowercase search term, "
            "with the id of the document that holds it.",
            _ref("dictionary-index"),
            [module],
        ),
        "/v1/{dictionary}/{entry}.json": _operation(
            "getDictionaryEntry",
            "dictionary",
            "One word of one dictionary",
            "The definition, the words it links to and from, and the scripture it cites.",
            _ref("dictionary-entry"),
            [module, _ENTRY_PARAMETER],
        ),
        "/v1/{dictionary}.json": _operation(
            "getDictionary",
            "dictionary",
            "The whole dictionary",
            "Every entry document in index order, each byte-for-byte the document "
            "served at its own path. A bulk document: metadata.json states its size.",
            _ref("dictionary"),
            [module],
        ),
    }


def _component_schemas(schemas_dir: Path, names: tuple[str, ...]) -> dict[str, Any]:
    """The published schemas, embedded so that every reference stays inside this document."""
    components: dict[str, Any] = {}
    for name in names:
        schema = read_json(schemas_dir / f"{name}.schema.json")
        schema.pop("$schema", None)
        schema.pop("$id", None)
        components[name] = _internalise(schema, name, names)
    return components


def _internalise(node: Any, own: str, names: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                result[key] = _component_reference(value, own, names)
            else:
                result[key] = _internalise(value, own, names)
        return result
    if isinstance(node, list):
        return [_internalise(value, own, names) for value in node]
    return node


def _component_reference(reference: str, own: str, names: tuple[str, ...]) -> str:
    if reference.startswith("#"):
        return f"#/components/schemas/{own}{reference[1:]}"
    target = reference.rsplit("/", 1)[-1].removesuffix(".json").removesuffix(".schema")
    if target not in names:
        raise ValueError(f"Schema {own} refers to {reference}, which this tree does not publish")
    return f"#/components/schemas/{target}"


__all__ = ["OPENAPI_VERSION", "SCHEMA_NAMES", "openapi_document"]
