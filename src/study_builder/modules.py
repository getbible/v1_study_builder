from __future__ import annotations

import shutil
import urllib.parse
from pathlib import Path

from study_builder.catalog import PACKAGE_URL
from study_builder.http import HttpClient
from study_builder.models import ModuleDescriptor
from study_builder.security import extract_zip
from study_builder.util import reset_directory, slug


class ModuleInstaller:
    def __init__(self, cache: Path, http: HttpClient, refresh: bool = False) -> None:
        self.cache = cache
        self.http = http
        self.refresh = refresh

    def install(self, module: ModuleDescriptor) -> Path:
        archives = self.cache / "archives"
        installations = self.cache / "installed"
        archive = archives / f"{slug(module.name)}.zip"
        installation = installations / slug(module.name)
        if self.refresh or not archive.exists():
            if "{module}" not in PACKAGE_URL:
                raise ValueError("STUDY_BUILDER_PACKAGE_URL must contain {module}")
            url = PACKAGE_URL.format(module=urllib.parse.quote(module.name, safe=""))
            self.http.download(url, archive)
        marker = installation / ".installed"
        archive_stamp = f"{archive.stat().st_size}:{archive.stat().st_mtime_ns}"
        if (
            not self.refresh
            and marker.exists()
            and marker.read_text(encoding="ascii") == archive_stamp
        ):
            return installation
        reset_directory(installation, boundary=installations)
        extract_zip(archive, installation)
        if not (installation / "mods.d").is_dir():
            candidates = list(installation.rglob("mods.d"))
            if len(candidates) == 1:
                nested_root = candidates[0].parent
                temporary = installation.with_name(installation.name + ".flattened")
                if temporary.exists():
                    shutil.rmtree(temporary)
                shutil.move(str(nested_root), temporary)
                shutil.rmtree(installation)
                shutil.move(temporary, installation)
            else:
                raise RuntimeError(
                    f"{module.name} package does not contain a unique mods.d directory"
                )
        marker.write_text(archive_stamp, encoding="ascii")
        return installation
