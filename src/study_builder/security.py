from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


class UnsafeArchiveError(ValueError):
    pass


def _safe_relative(name: str) -> Path:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise UnsafeArchiveError(f"Unsafe archive path: {name!r}")
    parts = [part for part in normalized.parts if part not in {"", "."}]
    if not parts:
        raise UnsafeArchiveError(f"Empty archive path: {name!r}")
    return Path(*parts)


def extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UnsafeArchiveError("ZIP contains too many members")
        total = sum(member.file_size for member in members)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UnsafeArchiveError("ZIP expands beyond the configured limit")
        for member in members:
            target = destination / _safe_relative(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"ZIP symlink is not allowed: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open("wb") as outgoing:
                while block := incoming.read(1024 * 1024):
                    outgoing.write(block)


def read_conf_files_from_tar(payload: bytes) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UnsafeArchiveError("TAR contains too many members")
        for member in members:
            _safe_relative(member.name)
            if not member.isfile() or not member.name.casefold().endswith(".conf"):
                continue
            if member.size > 4 * 1024 * 1024:
                raise UnsafeArchiveError(f"Configuration file is too large: {member.name}")
            total += member.size
            if total > 64 * 1024 * 1024:
                raise UnsafeArchiveError("Configuration catalog is too large")
            handle = archive.extractfile(member)
            if handle is None:
                continue
            results.append((member.name, handle.read().decode("utf-8", errors="replace")))
    return results
