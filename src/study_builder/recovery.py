# SPDX-License-Identifier: GPL-2.0-only
"""Recover complete, verified modules from the previous published tree."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import ValidationError, validate

from study_builder.models import ResourceKind
from study_builder.util import read_json, sha256_file, slug


class RecoveryError(RuntimeError):
    """Previous output cannot safely be used as a publication fallback."""


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError(f"Duplicate JSON member in previous output: {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise RecoveryError(f"Non-finite JSON value in previous output: {value}")


def _read(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(
                handle, object_pairs_hook=_unique_members, parse_constant=_invalid_constant
            )
    except (OSError, ValueError) as error:
        raise RecoveryError(f"Cannot read previous output {path.name}: {error}") from error


def _canonical_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
        or path.suffix != ".json"
    ):
        raise RecoveryError(f"Unsafe previous manifest path: {value!r}")


def _inventory(root: Path) -> set[str]:
    """Inspect every directory without following symlinks or accepting special files."""
    for directory in (root, *root.parents):
        if directory.is_symlink():
            raise RecoveryError(f"Unsafe previous output directory: {directory}")
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise RecoveryError(f"Previous output is not a regular directory: {directory}")
        for path in directory.iterdir():
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                files.add(path.relative_to(root).as_posix())
            else:
                raise RecoveryError(f"Unsafe previous output file: {path}")
    return files


class PreviousTree:
    """A prior tree whose catalog is trusted only after manifest verification.

    Global metadata and filesystem safety are checked when the tree is opened.
    Module inventories and bytes are checked on retention, so corrupt content in an
    unrelated module cannot prevent recovery of another complete module. No source
    text, coordinates, or extractor contract checks are relaxed by this fallback.
    """

    def __init__(
        self,
        root: Path,
        kind: ResourceKind,
        schemas_dir: Path,
        reserved_ids: frozenset[str],
    ) -> None:
        self.root = root
        self.kind = kind
        self.schemas_dir = schemas_dir
        self.reserved_ids = reserved_ids
        self.records: dict[str, dict[str, Any]] = {}
        self.files: dict[str, str] = {}
        if not root.exists() and not root.is_symlink():
            return
        actual = _inventory(root)
        manifest = self._document(root / "hashes.json", "hashes")
        self.files = manifest["files"]
        for name in self.files:
            _canonical_path(name)
        if "hashes.json" in self.files:
            raise RecoveryError("Previous manifest must not include itself")

        prefix = "commentary" if kind == "commentaries" else "dictionary"
        catalog_path = f"{kind}.json"
        self._verify(catalog_path)
        catalog = self._document(root / catalog_path, f"{prefix}-catalog")
        if catalog["module_count"] != len(catalog[kind]):
            raise RecoveryError("Previous catalog module_count does not match its records")
        for record in catalog[kind]:
            module_id = record["id"]
            self._check_id(module_id)
            if module_id in self.records:
                raise RecoveryError(f"Duplicate previous catalog module: {module_id}")
            self.records[module_id] = record

        # Only catalog-listed modules may be omitted or recovered independently.
        # Root metadata and schemas are shared and must have an exact inventory.
        global_actual = {name for name in actual if not self._module_owned(name)}
        global_expected = {name for name in self.files if not self._module_owned(name)}
        for name in global_actual | global_expected:
            first, separator, _ = name.partition("/")
            root_document = not separator and name.removesuffix(".json") in reserved_ids
            if not root_document and first != "schema":
                raise RecoveryError(f"Previous output has no catalog owner: {name}")
        if global_actual != global_expected | {"hashes.json"}:
            raise RecoveryError("Previous tree metadata inventory does not match hashes.json")
        for name in global_expected:
            self._verify(name)

    def _check_id(self, module_id: str) -> None:
        try:
            safe = slug(module_id) == module_id and module_id not in self.reserved_ids
        except ValueError:
            safe = False
        if not safe:
            raise RecoveryError(f"Unsafe previous module identifier: {module_id!r}")

    def _module_owned(self, name: str) -> bool:
        first, separator, _ = name.partition("/")
        if separator:
            return first in self.records
        return name.endswith(".json") and name[:-5] in self.records

    def _document(self, path: Path, schema: str) -> dict[str, Any]:
        document = _read(path)
        try:
            validate(document, read_json(self.schemas_dir / f"{schema}.schema.json"))
        except ValidationError as error:
            raise RecoveryError(f"Invalid previous {path.name}: {error.message}") from error
        return document

    def _verify(self, relative: str) -> None:
        path = self.root / relative
        if relative not in self.files or not path.is_file() or path.is_symlink():
            raise RecoveryError(f"Previous manifest is missing a regular file: {relative}")
        if sha256_file(path) != self.files[relative]:
            raise RecoveryError(f"Previous output SHA-256 mismatch: {relative}")

    def available(self, module_id: str) -> bool:
        self._check_id(module_id)
        return module_id in self.records

    def _linked_inventory(
        self, stage: Path, module_id: str, record: dict[str, Any], expected: set[str]
    ) -> None:
        """Check addressable parts through the small index, never parse composed modules."""
        prefix = "commentary" if self.kind == "commentaries" else "dictionary"
        index_name = "books" if self.kind == "commentaries" else "index"
        index = self._document(stage / module_id / f"{index_name}.json", f"{prefix}-{index_name}")
        if index[prefix] != module_id or any(
            index[key] != record[key] for key in ("name", "language")
        ):
            raise RecoveryError(f"Previous index identity disagrees: {module_id}")
        linked = {
            f"{module_id}.json",
            f"{module_id}/metadata.json",
            f"{module_id}/{index_name}.json",
        }
        if self.kind == "dictionaries":
            entries = index["entries"]
            identifiers: set[str] = set()
            for entry in entries:
                entry_id = entry["id"]
                path = f"{module_id}/{entry_id}.json"
                _canonical_path(path)
                if (
                    "/" in entry_id
                    or entry_id.casefold() in identifiers
                    or entry_id.casefold() in {"index", "metadata"}
                ):
                    raise RecoveryError(f"Unsafe or duplicate previous entry: {entry_id!r}")
                identifiers.add(entry_id.casefold())
                linked.add(path)
            counts = {
                "entry_count": len(entries),
                "unique_key_count": len({entry["key"].casefold() for entry in entries}),
            }
            if any(index[key] != value for key, value in counts.items()):
                raise RecoveryError(f"Previous dictionary index counts disagree: {module_id}")
        else:
            books = index["books"]
            book_numbers: set[int] = set()
            for book in books:
                number = book["book"]
                chapters = book["chapters"]
                if number in book_numbers or len(chapters) != len(set(chapters)):
                    raise RecoveryError(f"Duplicate previous book or chapter: {module_id}")
                book_numbers.add(number)
                linked.add(f"{module_id}/{number}.json")
                linked.update(f"{module_id}/{number}/{chapter}.json" for chapter in chapters)
            counts = {
                "book_count": len(books),
                "chapter_count": sum(len(book["chapters"]) for book in books),
                "entry_count": sum(book["entry_count"] for book in books),
            }
            if index["book_count"] != counts["book_count"]:
                raise RecoveryError(f"Previous books index counts disagree: {module_id}")
        if any(record[key] != value for key, value in counts.items()):
            raise RecoveryError(f"Previous catalog and index counts disagree: {module_id}")
        if linked != expected:
            raise RecoveryError(f"Previous index links do not match module files: {module_id}")

    def retain(self, module_id: str, destination_root: Path) -> dict[str, Any]:
        """Copy one verified module, never root metadata or another module's files.

        The caller provides an output root with no files for this module. Validation
        and copying happen in temporary storage; errors leave no partial module.
        """
        self._check_id(module_id)
        if module_id not in self.records:
            raise RecoveryError(f"No previous catalog record for module {module_id!r}")
        actual = _inventory(self.root)
        prefix = module_id + "/"
        complete = module_id + ".json"
        expected = {name for name in self.files if name.startswith(prefix) or name == complete}
        present = {name for name in actual if name.startswith(prefix) or name == complete}
        if expected != present:
            raise RecoveryError(f"Previous module inventory mismatch: {module_id}")
        index = "books.json" if self.kind == "commentaries" else "index.json"
        required = {prefix + "metadata.json", prefix + index, complete}
        if not required <= expected:
            raise RecoveryError(f"Previous module is incomplete: {module_id}")
        self._verify(f"{self.kind}.json")
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / module_id
        whole_destination = destination_root / complete
        if any(path.exists() or path.is_symlink() for path in (destination, whole_destination)):
            raise RecoveryError(f"Refusing to overwrite staged module: {module_id}")

        with tempfile.TemporaryDirectory(prefix=".retain-", dir=destination_root) as temporary:
            stage = Path(temporary)
            for relative in sorted(expected):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, target)
                if sha256_file(target) != self.files[relative]:
                    raise RecoveryError(f"Previous output SHA-256 mismatch: {relative}")
            schema_prefix = "commentary" if self.kind == "commentaries" else "dictionary"
            metadata = self._document(
                stage / module_id / "metadata.json", f"{schema_prefix}-metadata"
            )
            record = self.records[module_id]
            if any(metadata.get(key) != value for key, value in record.items()):
                raise RecoveryError(f"Previous catalog and metadata disagree: {module_id}")
            self._linked_inventory(stage, module_id, record, expected)
            if (stage / complete).stat().st_size != record["bytes"]:
                raise RecoveryError(f"Previous whole-module size disagrees: {module_id}")
            os.replace(stage / module_id, destination)
            try:
                os.replace(stage / complete, whole_destination)
            except OSError:
                shutil.rmtree(destination)
                raise
        return dict(record)
