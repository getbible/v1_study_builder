# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

from pathlib import Path

import pytest

from study_builder.engine import EngineManifest, GetBibleSwordManager


def test_manifest_pins_release_and_architectures(project_root: Path, monkeypatch) -> None:
    manifest = EngineManifest.load(project_root / "conf/getbiblesword.json")
    assert manifest.version == "0.1.0"
    assert manifest.tag == "v0.1.0"
    monkeypatch.setattr("study_builder.engine.platform.system", lambda: "Linux")
    monkeypatch.setattr("study_builder.engine.platform.machine", lambda: "x86_64")
    assert manifest.platform_asset() == "getbiblesword-0.1.0-linux-x86_64.tar.gz"


def test_checksum_sidecar_must_name_exact_asset(tmp_path: Path) -> None:
    sidecar = tmp_path / "asset.sha256"
    sidecar.write_text("a" * 64 + "  asset.tar.gz\n", encoding="ascii")
    assert GetBibleSwordManager._read_checksum(sidecar, "asset.tar.gz") == "a" * 64
    with pytest.raises(RuntimeError, match="Invalid release checksum"):
        GetBibleSwordManager._read_checksum(sidecar, "another.tar.gz")


def test_release_asset_url_is_scoped_to_pinned_repository(
    project_root: Path, tmp_path: Path
) -> None:
    manager = GetBibleSwordManager(project_root / "conf/getbiblesword.json", tmp_path)
    manager._validate_asset_url(
        "https://api.github.com/repos/getbible/getbiblesword/releases/assets/123"
    )
    with pytest.raises(RuntimeError, match="outside api.github.com"):
        manager._validate_asset_url("https://example.com/archive.tar.gz")
