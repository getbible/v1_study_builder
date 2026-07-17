from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_SLUG = re.compile(r"[^a-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    result = _SAFE_SLUG.sub("-", value.casefold()).strip("-.")
    if not result or result in {".", ".."}:
        raise ValueError(f"Cannot create a safe slug from {value!r}")
    return result


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


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


def write_hash_sidecars(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        sha1 = hashlib.sha1(data, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()
        path.with_suffix(path.suffix + ".sha").write_text(sha1 + "\n", encoding="ascii")
        hashes[relative] = sha256
    return hashes


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
