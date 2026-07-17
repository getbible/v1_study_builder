# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import tarfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from study_builder.http import HttpClient
from study_builder.util import read_json

_CHECKSUM = re.compile(r"^([0-9a-fA-F]{64})[ \t]+[*]?([^\r\n]+)$")
_MAX_BINARY_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class EngineManifest:
    repository: str
    version: str
    tag: str
    contract: str
    assets: dict[str, str]

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
        assets = {str(key): str(name) for key, name in dict(value["assets"]).items()}
        return cls(
            repository=str(value["repository"]),
            version=version,
            tag=tag,
            contract=str(value["contract"]),
            assets=assets,
        )

    def platform_asset(self) -> str:
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


class GetBibleSwordManager:
    def __init__(
        self,
        manifest_path: Path,
        work_dir: Path,
        http: HttpClient | None = None,
        token: str | None = None,
    ) -> None:
        self.manifest = EngineManifest.load(manifest_path)
        self.work_dir = work_dir
        self.http = http or HttpClient()
        self.token = token or self._environment_token()

    @staticmethod
    def _environment_token() -> str | None:
        for name in ("GETBIBLESWORD_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return None

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

        asset_name = self.manifest.platform_asset()
        checksum_name = f"{asset_name}.sha256"
        release = self._release()
        assets = {
            str(item.get("name")): str(item.get("url"))
            for item in release.get("assets", [])
            if isinstance(item, dict)
        }
        missing = sorted({asset_name, checksum_name} - assets.keys())
        if missing:
            raise RuntimeError(
                f"Release {self.manifest.tag} is missing required assets: {', '.join(missing)}"
            )
        for name in (asset_name, checksum_name):
            self._validate_asset_url(assets[name])

        downloads = self.work_dir / "downloads" / "getbiblesword" / self.manifest.version
        checksum_path = downloads / checksum_name
        archive_path = downloads / asset_name
        headers = self._asset_headers()
        self.http.download(assets[checksum_name], checksum_path, headers=headers)
        expected = self._read_checksum(checksum_path, asset_name)
        self.http.download(assets[asset_name], archive_path, expected, headers=headers)
        self._extract_binary(archive_path, destination)
        try:
            self.verify(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def _release(self) -> dict[str, Any]:
        url = (
            f"https://api.github.com/repos/{self.manifest.repository}/releases/tags/"
            f"{self.manifest.tag}"
        )
        try:
            payload = self.http.get_bytes(url, headers=self._api_headers())
            value = json.loads(payload)
        except Exception as error:
            token_hint = (
                " Set GETBIBLESWORD_TOKEN to a fine-grained token with Contents: read access"
                " when the release repository is private."
            )
            raise RuntimeError(
                f"Unable to resolve pinned getbiblesword release {self.manifest.tag}.{token_hint}"
            ) from error
        if not isinstance(value, dict) or value.get("tag_name") != self.manifest.tag:
            raise RuntimeError(f"GitHub returned an unexpected release for {self.manifest.tag}")
        return value

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _asset_headers(self) -> dict[str, str]:
        headers = self._api_headers()
        headers["Accept"] = "application/octet-stream"
        return headers

    def _validate_asset_url(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        prefix = f"/repos/{self.manifest.repository}/releases/assets/"
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise RuntimeError("GitHub returned a release asset outside api.github.com")
        if not parsed.path.startswith(prefix) or parsed.query or parsed.fragment:
            raise RuntimeError("GitHub returned an unexpected release asset URL")

    @staticmethod
    def _read_checksum(path: Path, expected_name: str) -> str:
        match = _CHECKSUM.fullmatch(path.read_text(encoding="ascii").strip())
        if match is None or match.group(2) != expected_name:
            raise RuntimeError(f"Invalid release checksum file for {expected_name}")
        return match.group(1).casefold()

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
