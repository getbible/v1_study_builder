# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.error
from pathlib import Path

import pytest

from study_builder.engine import EngineManifest, GetBibleSwordManager


def test_manifest_pins_release_and_architectures(project_root: Path, monkeypatch) -> None:
    manifest = EngineManifest.load(project_root / "conf/getbiblesword.json")
    assert manifest.version == "0.1.1"
    assert manifest.tag == "v0.1.1"
    monkeypatch.setattr("study_builder.engine.platform.system", lambda: "Linux")
    monkeypatch.setattr("study_builder.engine.platform.machine", lambda: "x86_64")
    asset = manifest.platform_asset()
    assert asset.name == "getbiblesword-0.1.1-linux-x86_64.tar.gz"
    assert asset.sha256 == "ef8f698e77918be439a39973f6d3d2307951ec6054bee389f4f3ef0c148a063d"


def test_release_asset_url_is_scoped_to_pinned_repository(
    project_root: Path, tmp_path: Path
) -> None:
    manager = GetBibleSwordManager(project_root / "conf/getbiblesword.json", tmp_path)
    manager._validate_asset_url(
        "https://github.com/getbible/getbiblesword/releases/download/"
        "v0.1.1/getbiblesword-0.1.1-linux-x86_64.tar.gz",
        "getbiblesword-0.1.1-linux-x86_64.tar.gz",
    )
    with pytest.raises(RuntimeError, match="outside github.com"):
        manager._validate_asset_url(
            "https://example.com/archive.tar.gz",
            "getbiblesword-0.1.1-linux-x86_64.tar.gz",
        )


def test_install_avoids_rate_limited_github_api(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as package:
        payload = b"test getbiblesword executable"
        member = tarfile.TarInfo("usr/bin/getbiblesword")
        member.mode = 0o755
        member.size = len(payload)
        package.addfile(member, io.BytesIO(payload))
    archive_bytes = archive.getvalue()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    manifest_value = json.loads(
        (project_root / "conf/getbiblesword.json").read_text(encoding="utf-8")
    )
    manifest_value["assets"]["linux-x86_64"]["sha256"] = archive_sha256
    manifest_path = tmp_path / "getbiblesword.json"
    manifest_path.write_text(json.dumps(manifest_value), encoding="utf-8")
    monkeypatch.setattr("study_builder.engine.platform.system", lambda: "Linux")
    monkeypatch.setattr("study_builder.engine.platform.machine", lambda: "x86_64")

    class RateLimitedApiHttp:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get_bytes(self, url, headers=None):
            raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)

        def download(self, url, target, expected_sha256=None, headers=None):
            self.urls.append(url)
            assert url.startswith(
                "https://github.com/getbible/getbiblesword/releases/download/v0.1.1/"
            )
            assert "api.github.com" not in url
            assert expected_sha256 == archive_sha256
            assert headers is None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive_bytes)
            return target

    http = RateLimitedApiHttp()
    manager = GetBibleSwordManager(manifest_path, tmp_path, http=http)
    monkeypatch.setattr(manager, "verify", lambda executable: {"path": str(executable)})

    installed = manager.install()

    assert installed.read_bytes() == b"test getbiblesword executable"
    assert http.urls == [manager.manifest.asset_url(manager.manifest.platform_asset())]
