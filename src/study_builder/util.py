from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

_SAFE_SLUG = re.compile(r"[^a-z0-9._-]+")

# A Git remote rejects a blob above 100 MB outright and warns above 50 MB. The
# builder warns at the first and stops below the second, catching an oversized
# document in the build, naming the file, rather than hours later in a rejected push.
DOCUMENT_WARNING_BYTES = 50 * 1024 * 1024
DOCUMENT_CEILING_BYTES = 95 * 1024 * 1024


class DocumentTooLarge(RuntimeError):
    """A generated document exceeds the size a Git remote will accept."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    result = _SAFE_SLUG.sub("-", value.casefold()).strip("-.")
    if not result or result in {".", ".."}:
        raise ValueError(f"Cannot create a safe slug from {value!r}")
    return result


def stable_json(value: Any) -> str:
    """Serialize without padding: references must not multiply indentation at each level."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = stable_json(value)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path, *, exclude: Collection[str] = ()) -> dict[str, str]:
    """Digest every generated document. Also the manifest of builder-owned paths."""
    excluded = set(exclude)
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        hashes[relative] = sha256_file(path)
    return hashes


def megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MB"


def enforce_document_ceiling(path: Path, ceiling: int = DOCUMENT_CEILING_BYTES) -> int:
    """Refuse to publish a document a Git remote would reject, and say which one."""
    size = path.stat().st_size
    if ceiling and size > ceiling:
        raise DocumentTooLarge(
            f"{path.name} is {megabytes(size)}, above the {megabytes(ceiling)} document "
            "ceiling. Publishing it would be rejected by the remote. Either the source "
            "module grew beyond what a single document can carry, or it is repeating "
            "content that should have been collapsed."
        )
    if size > DOCUMENT_WARNING_BYTES:
        LOG.warning(
            "%s is %s, above the %s a Git remote warns about",
            path.name,
            megabytes(size),
            megabytes(DOCUMENT_WARNING_BYTES),
        )
    return size


def write_composed_json(
    path: Path, header: dict[str, Any], member: str, sources: Sequence[Path]
) -> int:
    """Write an envelope whose array member embeds already-written documents.

    The members are streamed from disk rather than held in memory, and each one is
    embedded byte-for-byte. A composed document therefore contains its parts exactly
    as they are served individually, so one client parser handles every zoom level.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if member in header:
        raise ValueError(f"Composed member {member!r} is already present in the envelope")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, delete=False, prefix=f".{path.name}."
        ) as handle:
            temporary = Path(handle.name)
            handle.write(stable_json(header).rstrip()[:-1].encode("utf-8"))
            if header:
                handle.write(b",")
            handle.write(json.dumps(member, ensure_ascii=False).encode("utf-8") + b":[")
            for index, source in enumerate(sources):
                if index:
                    handle.write(b",")
                # Copy fixed-size blocks, not lines: compact books may occupy a
                # single very large line. Preserve every source byte, including LF.
                with source.open("rb") as reader:
                    shutil.copyfileobj(reader, handle, length=1024 * 1024)
            handle.write(b"]}\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path.stat().st_size


def replace_tree(source: Path, destination: Path) -> None:
    """Atomically publish a generated directory on the same filesystem."""
    source = source.resolve()
    destination_parent = destination.parent.resolve()
    destination_parent.mkdir(parents=True, exist_ok=True)
    if destination.name in {"", ".", ".."}:
        raise ValueError(f"Unsafe destination: {destination}")
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {destination}")
    temporary = destination_parent / f".{destination.name}.next"
    previous = destination_parent / f".{destination.name}.previous"
    for internal in (temporary, previous):
        if internal.exists():
            if internal.is_dir() and not internal.is_symlink():
                shutil.rmtree(internal)
            else:
                internal.unlink()
    shutil.copytree(source, temporary)
    if destination.exists():
        os.replace(destination, previous)
    os.replace(temporary, destination)
    if previous.exists():
        shutil.rmtree(previous)


def reset_directory(path: Path, *, boundary: Path) -> None:
    resolved = path.resolve()
    boundary_resolved = boundary.resolve()
    if resolved == boundary_resolved or boundary_resolved not in resolved.parents:
        raise ValueError(f"Refusing to reset {resolved}; it is not below {boundary_resolved}")
    if path.exists():
        if path.is_symlink():
            raise ValueError(f"Refusing to reset symlink: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True)
