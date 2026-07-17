from __future__ import annotations

import base64
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import validate

from study_builder.content import extract_osis_references, public_content
from study_builder.models import ModuleDescriptor, NativeExport
from study_builder.util import read_json, slug, write_json

_STRONG_KEY = re.compile(r"^(?:strong:)?([GH])?0*(\d{1,5})(?:!.*)?$", re.IGNORECASE)
_SAFE_ENTRY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


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


class DictionaryWriter:
    def __init__(self, root: Path, schema_path: Path) -> None:
        self.root = root
        self.schema = read_json(schema_path)

    def write(self, module: ModuleDescriptor, exported: NativeExport) -> dict[str, Any]:
        module_id = slug(module.name)
        module_root = self.root / module_id
        prefix = strong_prefix(module, exported.metadata)
        key_index: list[dict[str, Any]] = []
        shards: dict[str, list[dict[str, Any]]] = defaultdict(list)
        used_ids: dict[str, str] = {}

        ordered_entries = sorted(
            exported.entries, key=lambda entry: str(entry.get("key", "")).casefold()
        )
        for source in ordered_entries:
            key = str(source.get("key", "")).strip()
            if not key:
                continue
            content = public_content(source)
            if not content.get("text") and not content.get("html"):
                continue
            canonical = canonical_strong(key, prefix)
            entry_id = canonical or encoded_entry_id(key)
            collision_key = entry_id.casefold()
            if collision_key in used_ids and used_ids[collision_key] != key:
                entry_id = "h-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
            used_ids[entry_id.casefold()] = key
            aliases = sorted({value for value in (key, canonical) if value})
            raw = str(source.get("raw", ""))
            rendered = str(source.get("html", ""))
            document: dict[str, Any] = {
                "schema": "getbible-dictionary-entry-v1",
                "dictionary": module_id,
                "language": module.language,
                "id": entry_id,
                "key": key,
                "aliases": aliases,
                **content,
            }
            references = extract_osis_references(raw, rendered)
            if references:
                document["references"] = references
            validate(document, self.schema)
            write_json(module_root / f"{entry_id}.json", document)
            index_record = {
                "key": key,
                "id": entry_id,
                "aliases": aliases,
                "url": f"{entry_id}.json",
            }
            key_index.append(index_record)
            shard = hashlib.sha256(key.casefold().encode("utf-8")).hexdigest()[:2]
            shards[shard].append(index_record)

        for shard, records in sorted(shards.items()):
            write_json(module_root / "indexes" / f"{shard}.json", records)
        write_json(module_root / "keys.json", key_index)
        metadata = {
            "schema": "getbible-dictionary-metadata-v1",
            "id": module_id,
            "module": module.name,
            "name": module.description,
            "language": module.language,
            "version": module.version,
            "license": module.license,
            "driver": module.driver,
            "source_type": module.first("sourcetype"),
            "entry_count": len(key_index),
            "strong_prefix": prefix,
            "keys_url": "keys.json",
            "entry_url_template": "{entry}.json",
            "index_url_template": "indexes/{sha256_prefix}.json",
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
        return metadata
