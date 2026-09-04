# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from jsonschema import validate

from study_builder.books import BookRegistry
from study_builder.content import extract_osis_references, public_content
from study_builder.models import ModuleDescriptor, NativeExport
from study_builder.references import ReferenceParser
from study_builder.util import (
    DOCUMENT_CEILING_BYTES,
    enforce_document_ceiling,
    read_json,
    slug,
    write_composed_json,
    write_json,
)

_STRONG_KEY = re.compile(r"^(?:strong:)?([GH])?0*(\d{1,5})(?:!.*)?$", re.IGNORECASE)
_SAFE_ENTRY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_OSIS_LIKE = re.compile(r"^[1-4]?[A-Za-z][A-Za-z0-9]+\.\d+(?:\.\d+)?$")
_SWORD_LINK = re.compile(r"sword://(?P<value>[^\s\"'<>]+)", re.IGNORECASE)
_MARKUP_TARGET = re.compile(
    r"<(?:ref|reference|a)\b[^>]*?\b(?:target|href|osisRef)\s*=\s*[\"'](?P<value>[^\"']+)[\"']",
    re.IGNORECASE,
)
_STRONG_SEE = re.compile(
    r"\bsee\s+(?P<language>GREEK|HEBREW)\s+for\s+0*(?P<number>\d{1,5})\b", re.IGNORECASE
)
_SEARCH_NOISE = re.compile(r"[^\w\s-]", re.UNICODE)

# Reserved inside a dictionary directory; an entry may never claim these names.
RESERVED_DOCUMENTS = {"metadata.json", "index.json"}


def strong_prefix(module: ModuleDescriptor, metadata: dict[str, Any]) -> str | None:
    features = " ".join(
        [module.name, module.description, module.first("feature"), str(metadata.get("feature", ""))]
    ).casefold()
    if "greekdef" in features or ("strong" in features and "greek" in features):
        return "G"
    if "hebrewdef" in features or ("strong" in features and "hebrew" in features):
        return "H"
    return None


def canonical_strong(key: str, prefix: str | None) -> str | None:
    match = _STRONG_KEY.fullmatch(key.strip())
    if not match:
        return None
    selected = (match.group(1) or prefix or "").upper()
    if selected not in {"G", "H"}:
        return None
    number = match.group(2).lstrip("0") or "0"
    if selected == "H":
        return "H0" + number
    return "G" + number


def encoded_entry_id(key: str) -> str:
    if _SAFE_ENTRY_KEY.fullmatch(key):
        return "k-" + key
    payload = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
    if len(payload) <= 180:
        return "k-" + payload
    return "h-" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def search_key(key: str) -> str:
    """Fold a source key to an accent-insensitive, lowercase search term."""
    decomposed = unicodedata.normalize("NFKD", key)
    folded = "".join(part for part in decomposed if not unicodedata.combining(part)).casefold()
    return re.sub(r"\s+", " ", _SEARCH_NOISE.sub(" ", folded)).strip() or key.casefold()


def link_candidates(*values: str) -> set[str]:
    """Collect the raw cross-reference targets a dictionary entry points at.

    Source markup and the extractor's rendered form spell the same link in different
    ways, so both are scanned. Only targets that resolve to a key in the same
    dictionary survive, which keeps the sweep self-limiting.
    """
    candidates: set[str] = set()
    for value in values:
        for match in _SWORD_LINK.finditer(value):
            candidates.add(match.group("value").rsplit("/", 1)[-1])
        for match in _MARKUP_TARGET.finditer(value):
            target = match.group("value")
            if target.lower().startswith("sword://"):
                target = target[len("sword://") :]
            candidates.add(target.rsplit("/", 1)[-1].split(":", 1)[-1])
        for match in _STRONG_SEE.finditer(value):
            language = "G" if match.group("language").upper() == "GREEK" else "H"
            strong = canonical_strong(match.group("number"), language)
            if strong:
                candidates.add(strong)
    resolved = set()
    for candidate in candidates:
        value = unquote(candidate).strip().strip("#")
        # Scripture references belong in "references", not in the word link graph.
        if value and not _OSIS_LIKE.fullmatch(value):
            resolved.add(value)
    return resolved


@dataclass
class _Staged:
    entry_id: str
    key: str
    occurrence: int
    aliases: list[str]
    search: str
    targets: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)
    backlinks: list[str] = field(default_factory=list)


class DictionaryWriter:
    """Write the search index and per-word documents for one dictionary module.

    The build reads the validated entry spool twice: the first pass assigns stable
    identifiers and collects cross-reference targets, and the second writes each word
    with its links resolved. Forward and reverse links are only knowable once every
    key has an identifier, so a single pass cannot produce a navigable link graph.
    """

    def __init__(
        self,
        root: Path,
        books: BookRegistry,
        schemas_dir: Path,
        max_document_bytes: int = DOCUMENT_CEILING_BYTES,
    ) -> None:
        self.root = root
        self.books = books
        self.max_document_bytes = max_document_bytes
        self.schema = read_json(schemas_dir / "dictionary-entry.schema.json")
        self.parser: ReferenceParser | None = None

    def write(self, module: ModuleDescriptor, exported: NativeExport) -> tuple[dict, dict]:
        module_id = slug(module.name)
        module_root = self.root / module_id
        prefix = strong_prefix(module, exported.metadata)
        self.parser = ReferenceParser(self.books, module.language)

        staged = self._stage(exported, prefix)
        self._resolve_links(staged)

        by_id = {item.entry_id: item for item in staged}
        entry_files: dict[str, Path] = {}
        position = 0
        for source in exported.entries:
            content = self._publishable(source)
            if content is None:
                continue
            item = staged[position]
            position += 1
            path = module_root / f"{item.entry_id}.json"
            if path.name in RESERVED_DOCUMENTS:
                raise RuntimeError(f"Entry document collides with a reserved name: {path.name}")
            write_json(path, self._document(module, module_id, item, content, source, by_id))
            entry_files[item.entry_id] = path
        if position != len(staged):
            raise RuntimeError(
                f"Dictionary entries changed between passes: staged {len(staged)}, wrote {position}"
            )

        index = sorted(staged, key=lambda item: (item.search, item.key.casefold(), item.occurrence))
        unique_keys = len({item.key.casefold() for item in staged})
        write_json(
            module_root / "index.json",
            {
                "schema": "getbible-dictionary-index-v1",
                "dictionary": module_id,
                "language": module.language,
                "name": module.description,
                "entry_url_template": "{entry}.json",
                "entry_count": len(staged),
                "unique_key_count": unique_keys,
                "entries": [self._index_record(item) for item in index],
            },
        )

        complete = self.root / f"{module_id}.json"
        write_composed_json(
            complete,
            {
                "schema": "getbible-dictionary-v1",
                "dictionary": module_id,
                "language": module.language,
                "name": module.description,
            },
            "entries",
            [entry_files[item.entry_id] for item in index],
        )
        complete_bytes = enforce_document_ceiling(complete, self.max_document_bytes)

        metadata = self._metadata(
            module, module_id, prefix, len(staged), unique_keys, complete_bytes
        )
        write_json(module_root / "metadata.json", metadata)
        record = {
            "id": module_id,
            "name": module.description,
            "language": module.language,
            "license": module.license,
            "entry_count": len(staged),
            "unique_key_count": unique_keys,
            "strong_prefix": prefix,
            "bytes": metadata["bytes"],
        }
        return record, metadata

    @staticmethod
    def _publishable(source: dict[str, Any]) -> dict[str, str] | None:
        if not str(source.get("key", "")).strip():
            return None
        content = public_content(source)
        return content if content["text"] else None

    def _stage(self, exported: NativeExport, prefix: str | None) -> list[_Staged]:
        staged: list[_Staged] = []
        used_ids: dict[str, str] = {}
        occurrences: dict[str, int] = defaultdict(int)
        for source in exported.entries:
            if self._publishable(source) is None:
                continue
            key = str(source["key"]).strip()
            canonical = canonical_strong(key, prefix)
            entry_id = canonical or encoded_entry_id(key)
            collision_key = entry_id.casefold()
            if collision_key in used_ids and used_ids[collision_key] != key:
                entry_id = "h-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
                collision_key = entry_id.casefold()
            occurrences[collision_key] += 1
            occurrence = occurrences[collision_key]
            if occurrence > 1:
                entry_id = f"{entry_id}--{occurrence}"
            used_ids[entry_id.casefold()] = key
            staged.append(
                _Staged(
                    entry_id=entry_id,
                    key=key,
                    occurrence=occurrence,
                    aliases=sorted({value for value in (key, canonical) if value}),
                    search=search_key(key),
                    targets=link_candidates(
                        str(source.get("raw", "")), str(source.get("html", ""))
                    ),
                )
            )
        return staged

    @staticmethod
    def _resolve_links(staged: list[_Staged]) -> None:
        """Turn raw targets into entry identifiers, then invert them into backlinks."""
        lookup: dict[str, str] = {}
        for item in staged:
            for alias in (item.key, *item.aliases, item.search):
                lookup.setdefault(alias.casefold(), item.entry_id)
        incoming: dict[str, list[str]] = defaultdict(list)
        for item in staged:
            seen: set[str] = set()
            for target in sorted(item.targets):
                resolved = lookup.get(target.casefold()) or lookup.get(search_key(target))
                if not resolved or resolved == item.entry_id or resolved in seen:
                    continue
                seen.add(resolved)
                item.links.append(resolved)
                incoming[resolved].append(item.entry_id)
            item.links.sort()
            item.targets = set()  # released once resolved; only the ids are published
        for item in staged:
            item.backlinks = sorted(set(incoming.get(item.entry_id, ())))

    def _document(
        self,
        module: ModuleDescriptor,
        module_id: str,
        item: _Staged,
        content: dict[str, str],
        source: dict[str, Any],
        by_id: dict[str, _Staged],
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema": "getbible-dictionary-entry-v1",
            "dictionary": module_id,
            "language": module.language,
            "id": item.entry_id,
            "key": item.key,
            "occurrence": item.occurrence,
            "aliases": item.aliases,
            **content,
        }
        if item.links:
            document["see_also"] = [
                {"id": target, "key": by_id[target].key} for target in item.links
            ]
        if item.backlinks:
            document["backlinks"] = [
                {"id": target, "key": by_id[target].key} for target in item.backlinks
            ]
        references = []
        for reference in extract_osis_references(
            str(source.get("raw", "")), str(source.get("html", ""))
        ):
            normalized = self.books.reference(reference)
            if normalized:
                references.append(normalized)
        if not references and self.parser is not None:
            # The source did not mark its references up, so they exist only as
            # prose. Recognise them there, so a client can still link the word to
            # the Bible without parsing the text itself.
            references = self.parser.extract(content["text"])
        if references:
            document["references"] = references
        validate(document, self.schema)
        return document

    @staticmethod
    def _index_record(item: _Staged) -> dict[str, Any]:
        record: dict[str, Any] = {"id": item.entry_id, "key": item.key, "search": item.search}
        if item.aliases != [item.key]:
            record["aliases"] = item.aliases
        if item.occurrence > 1:
            record["occurrence"] = item.occurrence
        return record

    @staticmethod
    def _metadata(
        module: ModuleDescriptor,
        module_id: str,
        prefix: str | None,
        entry_count: int,
        unique_key_count: int,
        complete_bytes: int,
    ) -> dict[str, Any]:
        return {
            "schema": "getbible-dictionary-metadata-v1",
            "id": module_id,
            "module": module.name,
            "name": module.description,
            "language": module.language,
            "version": module.version,
            "license": module.license,
            "driver": module.driver,
            "source_type": module.first("sourcetype"),
            "entry_count": entry_count,
            "unique_key_count": unique_key_count,
            "strong_prefix": prefix,
            "bytes": complete_bytes,
            "index_url": "index.json",
            "entry_url_template": "{entry}.json",
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
