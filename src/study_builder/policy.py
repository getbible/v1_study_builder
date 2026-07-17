from __future__ import annotations

from pathlib import Path

from study_builder.catalog import normalize_license
from study_builder.models import ModuleDescriptor, PolicyDecision
from study_builder.util import read_json


class ModulePolicy:
    def __init__(self, path: Path) -> None:
        raw = read_json(path)
        self.approved_licenses = {
            normalize_license(item) for item in raw.get("approved_license_values", [])
        }
        self.approved_modules = {
            key.casefold(): value for key, value in raw.get("approved_modules", {}).items()
        }
        self.denied_modules = {
            key.casefold(): value for key, value in raw.get("denied_modules", {}).items()
        }

    def decide(self, module: ModuleDescriptor) -> PolicyDecision:
        key = module.name.casefold()
        if key in self.denied_modules:
            return PolicyDecision(False, f"explicit denial: {self.denied_modules[key]}")
        if key in self.approved_modules:
            note = self.approved_modules[key]
            return PolicyDecision(True, f"explicit approval: {note}")
        license_key = normalize_license(module.license)
        if license_key and license_key in self.approved_licenses:
            return PolicyDecision(True, f"approved license: {module.license}")
        license_label = module.license or "not declared"
        return PolicyDecision(False, f"license requires review: {license_label}")
