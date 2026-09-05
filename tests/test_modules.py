# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from study_builder.models import ModuleDescriptor
from study_builder.modules import ModuleInstaller


def test_offline_installer_rejects_missing_package_without_downloading(tmp_path: Path) -> None:
    http = Mock()
    installer = ModuleInstaller(tmp_path, http, offline=True)
    module = ModuleDescriptor(name="Demo", fields={}, conf_path="mods.d/demo.conf")

    with pytest.raises(RuntimeError, match="no cached package exists for Demo"):
        installer.install(module)

    http.download.assert_not_called()


def test_offline_installer_extracts_cached_package_without_downloading(tmp_path: Path) -> None:
    archive = tmp_path / "archives/demo.zip"
    archive.parent.mkdir()
    with ZipFile(archive, "w") as handle:
        handle.writestr("mods.d/demo.conf", "[Demo]\n")
        handle.writestr("modules/demo/data", b"module data")
    http = Mock()
    installer = ModuleInstaller(tmp_path, http, offline=True)
    module = ModuleDescriptor(name="Demo", fields={}, conf_path="mods.d/demo.conf")

    installation = installer.install(module)

    assert (installation / "modules/demo/data").read_bytes() == b"module data"
    assert installer.install(module) == installation
    http.download.assert_not_called()
