from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ResourceKind = Literal["commentaries", "dictionaries"]


@dataclass(frozen=True)
class ModuleDescriptor:
    name: str
    fields: dict[str, tuple[str, ...]]
    conf_path: str

    def first(self, key: str, default: str = "") -> str:
        values = self.fields.get(key.casefold(), ())
        return values[-1].strip() if values else default

    @property
    def description(self) -> str:
        return self.first("description", self.name)

    @property
    def language(self) -> str:
        return self.first("lang", "und").lower()

    @property
    def driver(self) -> str:
        return self.first("moddrv")

    @property
    def category(self) -> str:
        return self.first("category")

    @property
    def license(self) -> str:
        return self.first("distributionlicense")

    @property
    def version(self) -> str:
        return self.first("version")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class NativeExport:
    metadata: dict[str, Any]
    entries: list[dict[str, Any]]
    diagnostics: tuple[dict[str, Any], ...] = ()
    footer: dict[str, Any] = field(default_factory=dict)


@dataclass
class BuildReport:
    started_at: str
    requested_resource: str
    catalog_url: str
    completed_at: str | None = None
    built: dict[str, list[str]] = field(
        default_factory=lambda: {"commentaries": [], "dictionaries": []}
    )
    skipped: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    diagnostics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    commits: dict[str, str | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "requested_resource": self.requested_resource,
            "catalog_url": self.catalog_url,
            "built": self.built,
            "skipped": self.skipped,
            "failed": self.failed,
            "diagnostics": self.diagnostics,
            "commits": self.commits,
        }


@dataclass(frozen=True)
class BuildPaths:
    root: Path
    work: Path
    dist: Path
    policy: Path
    books: Path
    schemas: Path
    engine: Path
