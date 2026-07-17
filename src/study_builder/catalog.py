from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable

from study_builder.models import ModuleDescriptor, ResourceKind
from study_builder.security import read_conf_files_from_tar

CATALOG_URL = os.environ.get(
    "STUDY_BUILDER_CATALOG_URL",
    "https://crosswire.org/ftpmirror/pub/sword/raw/mods.d.tar.gz",
)
PACKAGE_URL = os.environ.get(
    "STUDY_BUILDER_PACKAGE_URL",
    "https://crosswire.org/ftpmirror/pub/sword/packages/rawzip/{module}.zip",
)

COMMENTARY_DRIVERS = {"rawcom", "rawcom4", "zcom", "zcom4", "rawfiles"}
DICTIONARY_DRIVERS = {"rawld", "rawld4", "zld", "rawfiles"}


def parse_sword_conf(path: str, content: str) -> ModuleDescriptor:
    section = ""
    fields: dict[str, list[str]] = defaultdict(list)
    pending_key: str | None = None
    pending_value = ""

    def flush() -> None:
        nonlocal pending_key, pending_value
        if pending_key is not None:
            fields[pending_key].append(pending_value.strip())
        pending_key = None
        pending_value = ""

    for raw_line in content.replace("\ufeff", "").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            section = line[1:-1].strip()
            continue
        if "=" in line and not line[:1].isspace():
            flush()
            key, value = line.split("=", 1)
            pending_key = key.strip().casefold()
            pending_value = value.rstrip("\\").strip()
            if not line.endswith("\\"):
                flush()
            continue
        if pending_key is not None:
            pending_value += " " + line.rstrip("\\").strip()
            if not line.endswith("\\"):
                flush()
    flush()
    if not section:
        raise ValueError(f"No module section in {path}")
    return ModuleDescriptor(
        name=section,
        fields={key: tuple(values) for key, values in fields.items()},
        conf_path=path,
    )


def load_catalog(payload: bytes) -> list[ModuleDescriptor]:
    modules = [
        parse_sword_conf(path, content) for path, content in read_conf_files_from_tar(payload)
    ]
    return sorted(modules, key=lambda module: module.name.casefold())


def classify(module: ModuleDescriptor) -> ResourceKind | None:
    driver = module.driver.casefold()
    category = module.category.casefold()
    module_type = module.first("modtype").casefold()
    if driver in COMMENTARY_DRIVERS and (
        "comment" in category or "comment" in module_type or driver != "rawfiles"
    ):
        return "commentaries"
    if driver in DICTIONARY_DRIVERS and (
        "diction" in category
        or "lexicon" in category
        or "diction" in module_type
        or "lexicon" in module_type
        or driver != "rawfiles"
    ):
        return "dictionaries"
    return None


def select_modules(
    modules: Iterable[ModuleDescriptor],
    resource: str,
    requested: set[str] | None = None,
) -> list[tuple[ResourceKind, ModuleDescriptor]]:
    normalized_requested = {name.casefold() for name in requested or set()}
    selected: list[tuple[ResourceKind, ModuleDescriptor]] = []
    for module in modules:
        kind = classify(module)
        if kind is None or (resource != "all" and kind != resource):
            continue
        if normalized_requested and module.name.casefold() not in normalized_requested:
            continue
        selected.append((kind, module))
    if normalized_requested:
        found = {module.name.casefold() for _, module in selected}
        missing = sorted(normalized_requested - found)
        if missing:
            raise ValueError(
                f"Requested modules not found in selected resource: {', '.join(missing)}"
            )
    return selected


def normalize_license(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()
