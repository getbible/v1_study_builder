from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from study_builder import __version__
from study_builder.books import BookRegistry
from study_builder.catalog import CATALOG_URL, load_catalog, select_modules
from study_builder.commentaries import CommentaryWriter
from study_builder.dictionaries import DictionaryWriter
from study_builder.engine import GetBibleSwordManager
from study_builder.git import GitRepository, sign_commits_from_environment
from study_builder.http import HttpClient
from study_builder.models import BuildReport, ModuleDescriptor, ResourceKind
from study_builder.modules import ModuleInstaller
from study_builder.native import SwordExporter
from study_builder.policy import ModulePolicy
from study_builder.util import (
    replace_tree,
    reset_directory,
    slug,
    utc_now,
    write_hash_sidecars,
    write_json,
)

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineConfig:
    root: Path
    work_dir: Path
    dist_dir: Path
    policy_path: Path
    books_path: Path
    schemas_dir: Path
    engine_manifest_path: Path
    engine_schema_path: Path
    engine_path: Path | None = None
    resource: str = "all"
    modules: frozenset[str] = frozenset()
    refresh: bool = False
    offline: bool = False
    pull: bool = False
    push: bool = False
    dry_run: bool = False
    commentaries_repo: str = "git@github.com:getbible/v1_commentaries.git"
    dictionaries_repo: str = "git@github.com:getbible/v1_dictionaries.git"
    commentaries_branch: str = "main"
    dictionaries_branch: str = "main"


class BuildPipeline:
    def __init__(self, config: PipelineConfig, http: HttpClient | None = None) -> None:
        self.config = config
        self.http = http or HttpClient()
        self.policy = ModulePolicy(config.policy_path)
        self.books = BookRegistry(config.books_path)
        self.engine = GetBibleSwordManager(
            config.engine_manifest_path, config.work_dir, http=self.http
        )

    def _catalog(self) -> list[ModuleDescriptor]:
        cache = self.config.work_dir / "catalog" / "mods.d.tar.gz"
        if self.config.offline:
            if not cache.exists():
                raise RuntimeError("Offline mode requested but no cached CrossWire catalog exists")
        elif self.config.refresh or not cache.exists():
            self.http.download(CATALOG_URL, cache)
        return load_catalog(cache.read_bytes())

    def _repositories(self) -> dict[ResourceKind, GitRepository]:
        return {
            "commentaries": GitRepository(
                self.config.commentaries_repo,
                self.config.work_dir / "repos" / "v1_commentaries",
                self.config.commentaries_branch,
            ),
            "dictionaries": GitRepository(
                self.config.dictionaries_repo,
                self.config.work_dir / "repos" / "v1_dictionaries",
                self.config.dictionaries_branch,
            ),
        }

    def run(self) -> BuildReport:
        if self.config.push and self.config.modules:
            raise ValueError(
                "Publishing a partial --module build is disabled to protect the public API"
            )
        report = BuildReport(utc_now(), self.config.resource, CATALOG_URL)
        catalog = self._catalog()
        selected = select_modules(catalog, self.config.resource, set(self.config.modules))
        approved: list[tuple[ResourceKind, ModuleDescriptor]] = []
        for kind, module in selected:
            decision = self.policy.decide(module)
            if decision.allowed:
                approved.append((kind, module))
            else:
                report.skipped.append(
                    {"resource": kind, "module": module.name, "reason": decision.reason}
                )
        if not approved:
            raise RuntimeError("The policy did not approve any selected modules")
        identifiers: dict[tuple[ResourceKind, str], str] = {}
        for kind, module in approved:
            key = (kind, slug(module.name))
            if key in identifiers and identifiers[key] != module.name:
                raise RuntimeError(
                    f"Module identifiers collide after normalization: "
                    f"{identifiers[key]!r} and {module.name!r}"
                )
            identifiers[key] = module.name
        if self.config.dry_run:
            report.built = {
                "commentaries": [m.name for kind, m in approved if kind == "commentaries"],
                "dictionaries": [m.name for kind, m in approved if kind == "dictionaries"],
            }
            report.completed_at = utc_now()
            return report

        resources = sorted({kind for kind, _ in approved})
        generated_roots: dict[ResourceKind, Path] = {}
        for kind in resources:
            path = self.config.work_dir / "generated" / kind / "v1"
            reset_directory(path, boundary=self.config.work_dir / "generated")
            generated_roots[kind] = path

        installer = ModuleInstaller(
            self.config.work_dir / "modules", self.http, refresh=self.config.refresh
        )
        executable = self.engine.ensure(self.config.engine_path)
        exporter = SwordExporter(
            executable,
            self.config.engine_schema_path,
            self.engine.manifest.contract,
        )
        summaries: dict[ResourceKind, list[dict[str, Any]]] = {
            "commentaries": [],
            "dictionaries": [],
        }
        for kind, module in approved:
            LOG.info("Building %s module %s", kind, module.name)
            exported = None
            try:
                installation = installer.install(module)
                exported = exporter.export(installation, module.name)
                expected_classification = (
                    "commentary" if kind == "commentaries" else "dictionary_or_lexicon"
                )
                if exported.metadata.get("classification") != expected_classification:
                    raise RuntimeError(
                        f"getbiblesword classified {module.name} as "
                        f"{exported.metadata.get('classification')!r}, expected "
                        f"{expected_classification!r}"
                    )
                if exported.diagnostics:
                    report.diagnostics[module.name] = [
                        {
                            "sequence": item.get("sequence"),
                            "severity": item.get("severity"),
                            "code": item.get("code"),
                            "message": item.get("message_text", ""),
                        }
                        for item in exported.diagnostics
                    ]
                if kind == "commentaries":
                    writer = CommentaryWriter(
                        generated_roots[kind],
                        self.books,
                        self.config.schemas_dir / "commentary-chapter.schema.json",
                    )
                else:
                    writer = DictionaryWriter(
                        generated_roots[kind],
                        self.config.schemas_dir / "dictionary-entry.schema.json",
                    )
                summary = writer.write(module, exported)
                summaries[kind].append(summary)
                report.built[kind].append(module.name)
            except Exception as error:
                LOG.exception("Failed to build %s", module.name)
                report.failed.append(
                    {"resource": kind, "module": module.name, "reason": str(error)}
                )
            finally:
                if exported is not None:
                    exported.close()
        if report.failed:
            report.completed_at = utc_now()
            self._write_report(report)
            names = ", ".join(item["module"] for item in report.failed)
            raise RuntimeError(f"Build failed; no output was published. Failed modules: {names}")

        generated_at = utc_now()
        for kind in resources:
            records = sorted(summaries[kind], key=lambda item: item["id"])
            catalog_name = kind
            base_url = (
                "https://commentaries.getbible.net/v1/"
                if kind == "commentaries"
                else "https://dictionaries.getbible.net/v1/"
            )
            write_json(
                generated_roots[kind] / f"{catalog_name}.json",
                {
                    "schema": f"getbible-{kind}-catalog-v1",
                    "version": 1,
                    "generated_at": generated_at,
                    "base_url": base_url,
                    kind: records,
                },
            )
            write_json(
                generated_roots[kind] / "build.json",
                {
                    "builder": "v1_study_builder",
                    "builder_version": __version__,
                    "extractor": "getbiblesword",
                    "extractor_version": self.engine.manifest.version,
                    "extractor_contract": self.engine.manifest.contract,
                    "api_version": 1,
                    "resource": kind,
                    "generated_at": generated_at,
                    "catalog_url": CATALOG_URL,
                    "module_count": len(records),
                },
            )
            hashes = write_hash_sidecars(generated_roots[kind])
            write_json(
                generated_roots[kind] / "hashes.json",
                {"algorithm": "sha256", "files": hashes},
            )
            write_hash_sidecars(generated_roots[kind])
            dist_root = self.config.dist_dir / kind / "v1"
            replace_tree(generated_roots[kind], dist_root)

        if self.config.pull or self.config.push:
            repositories = self._repositories()
            sign = sign_commits_from_environment()
            for kind in resources:
                repository = repositories[kind]
                repository.prepare(self.config.pull)
                replace_tree(generated_roots[kind], repository.path / "v1")
                commit = repository.commit(f"Build {kind} API v1 ({generated_at})", sign=sign)
                report.commits[kind] = commit
                if self.config.push:
                    repository.push()

        report.completed_at = utc_now()
        self._write_report(report)
        return report

    def _write_report(self, report: BuildReport) -> None:
        write_json(self.config.work_dir / "reports" / "latest.json", report.as_dict())
