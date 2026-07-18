# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import tarfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from study_builder.http import HttpClient
from study_builder.util import read_json

_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_BINARY_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class EngineAsset:
    name: str
    sha256: str


@dataclass(frozen=True)
class EngineManifest:
    repository: str
    version: str
    tag: str
    contract: str
    assets: dict[str, EngineAsset]

    @classmethod
    def load(cls, path: Path) -> EngineManifest:
        value = read_json(path)
        required = {"repository", "version", "tag", "contract", "assets"}
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(f"getbiblesword manifest is missing: {', '.join(missing)}")
        version = str(value["version"])
        tag = str(value["tag"])
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("getbiblesword version must be semantic")
        if tag != f"v{version}":
            raise ValueError("getbiblesword tag must match its pinned version")
        repository = str(value["repository"])
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("getbiblesword repository must be an owner/name pair")
        assets: dict[str, EngineAsset] = {}
        for key, raw_asset in dict(value["assets"]).items():
            if not isinstance(raw_asset, dict):
                raise ValueError(f"getbiblesword asset {key} must pin a name and SHA-256")
            name = str(raw_asset.get("name", ""))
            sha256 = str(raw_asset.get("sha256", "")).casefold()
            if not _ASSET_NAME.fullmatch(name):
                raise ValueError(f"getbiblesword asset {key} has an invalid filename")
            if not _DIGEST.fullmatch(sha256):
                raise ValueError(f"getbiblesword asset {key} has an invalid SHA-256")
            assets[str(key)] = EngineAsset(name=name, sha256=sha256)
        return cls(
            repository=repository,
            version=version,
            tag=tag,
            contract=str(value["contract"]),
            assets=assets,
        )

    def platform_asset(self) -> EngineAsset:
        if platform.system().casefold() != "linux":
            raise RuntimeError("The pinned getbiblesword release currently supports Linux only")
        machine = platform.machine().casefold()
        architecture = {
            "amd64": "x86_64",
            "x86_64": "x86_64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }.get(machine)
        if architecture is None:
            raise RuntimeError(f"Unsupported getbiblesword architecture: {machine}")
        key = f"linux-{architecture}"
        try:
            return self.assets[key]
        except KeyError as error:
            raise RuntimeError(f"No pinned getbiblesword release asset for {key}") from error

    def asset_url(self, asset: EngineAsset) -> str:
        return f"https://github.com/{self.repository}/releases/download/{self.tag}/{asset.name}"


class GetBibleSwordManager:
    def __init__(
        self,
        manifest_path: Path,
        work_dir: Path,
        http: HttpClient | None = None,
    ) -> None:
        self.manifest = EngineManifest.load(manifest_path)
        self.work_dir = work_dir
        self.http = http or HttpClient()

    @property
    def executable(self) -> Path:
        return (
            self.work_dir
            / "tools"
            / "getbiblesword"
            / self.manifest.version
            / "bin"
            / "getbiblesword"
        )

    def ensure(self, requested: Path | None = None, force: bool = False) -> Path:
        if requested is not None:
            executable = requested.resolve()
            self.verify(executable)
            return executable
        executable = self.executable
        if force or not executable.is_file():
            self.install(force=force)
        self.verify(executable)
        return executable

    def install(self, force: bool = False) -> Path:
        destination = self.executable
        if destination.is_file() and not force:
            self.verify(destination)
            return destination

        asset = self.manifest.platform_asset()
        asset_url = self.manifest.asset_url(asset)
        self._validate_asset_url(asset_url, asset.name)

        downloads = self.work_dir / "downloads" / "getbiblesword" / self.manifest.version
        archive_path = downloads / asset.name
        self.http.download(asset_url, archive_path, asset.sha256)
        self._extract_binary(archive_path, destination)
        try:
            self.verify(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def _validate_asset_url(self, url: str, asset_name: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        expected_path = (
            f"/{self.manifest.repository}/releases/download/{self.manifest.tag}/{asset_name}"
        )
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            raise RuntimeError("Release asset is outside github.com")
        if parsed.path != expected_path or parsed.query or parsed.fragment:
            raise RuntimeError("Release asset URL does not match the pinned release")

    @staticmethod
    def _extract_binary(archive_path: Path, destination: Path) -> None:
        selected = None
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                normalized = tuple(part for part in path.parts if part not in {"", "."})
                if path.is_absolute() or ".." in normalized:
                    raise RuntimeError(f"Unsafe path in getbiblesword archive: {member.name}")
                if normalized == ("usr", "bin", "getbiblesword"):
                    selected = member
            if selected is None or not selected.isfile():
                raise RuntimeError("The release archive does not contain usr/bin/getbiblesword")
            if selected.size <= 0 or selected.size > _MAX_BINARY_BYTES:
                raise RuntimeError("The released getbiblesword executable has an invalid size")
            source = archive.extractfile(selected)
            if source is None:
                raise RuntimeError("Unable to read getbiblesword from its release archive")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".partial")
            digest = hashlib.sha256()
            size = 0
            try:
                with source, temporary.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        if size > _MAX_BINARY_BYTES:
                            raise RuntimeError("The released getbiblesword executable is too large")
                        digest.update(block)
                        output.write(block)
                if size != selected.size:
                    raise RuntimeError(
                        "The getbiblesword executable was truncated during extraction"
                    )
                temporary.chmod(0o755)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

    def verify(self, executable: Path) -> dict[str, str]:
        if not executable.is_file():
            raise FileNotFoundError(f"getbiblesword executable not found: {executable}")
        version = self._run(executable, "version")
        expected_prefix = f"getBibleSword {self.manifest.version} "
        if not version.startswith(expected_prefix):
            raise RuntimeError(
                f"Expected getbiblesword {self.manifest.version}, "
                f"received: {version.splitlines()[0]}"
            )
        contract_output = self._run(executable, "contract")
        try:
            contract = json.loads(contract_output)
        except json.JSONDecodeError as error:
            raise RuntimeError("getbiblesword returned invalid contract metadata") from error
        if contract.get("contract") != self.manifest.contract:
            raise RuntimeError(
                f"Expected contract {self.manifest.contract}, received {contract.get('contract')!r}"
            )
        return {
            "path": str(executable.resolve()),
            "version": self.manifest.version,
            "contract": self.manifest.contract,
        }

    @staticmethod
    def _run(executable: Path, command: str) -> str:
        try:
            result = subprocess.run(
                [str(executable), command],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise RuntimeError(f"Unable to run {executable} {command}: {detail.strip()}") from error
        return result.stdout.strip()
