from __future__ import annotations

import errno
import logging
import os
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, validate

from study_builder import __version__
from study_builder.bible import DEFAULT_BIBLE_API, BibleApi
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
from study_builder.openapi import SCHEMA_NAMES, openapi_document
from study_builder.policy import ModulePolicy
from study_builder.recovery import PreviousTree
from study_builder.references import ReferenceEngine
from study_builder.util import (
    DOCUMENT_CEILING_BYTES,
    hash_tree,
    read_json,
    replace_tree,
    reset_directory,
    slug,
    utc_now,
    write_json,
)

LOG = logging.getLogger(__name__)

# A module directory and its whole-module document both sit at the v1 root, so a
# module identifier may never collide with a document the builder writes there.
RESERVED_MODULE_IDS = frozenset(
    {"build", "build-report", "commentaries", "dictionaries", "hashes", "openapi", "schema"}
)

BASE_URLS: dict[str, str] = {
    "commentaries": "https://commentaries.getbible.net/v1/",
    "dictionaries": "https://dictionaries.getbible.net/v1/",
}

CATALOG_TEMPLATES: dict[str, dict[str, str]] = {
    "commentaries": {
        "metadata_url_template": "{commentary}/metadata.json",
        "books_url_template": "{commentary}/books.json",
        "commentary_url_template": "{commentary}.json",
        "book_url_template": "{commentary}/{book}.json",
        "chapter_url_template": "{commentary}/{book}/{chapter}.json",
    },
    "dictionaries": {
        "metadata_url_template": "{dictionary}/metadata.json",
        "index_url_template": "{dictionary}/index.json",
        "dictionary_url_template": "{dictionary}.json",
        "entry_url_template": "{dictionary}/{entry}.json",
    },
}


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
    aliases_dir: Path | None = None
    bible_api: str = DEFAULT_BIBLE_API
    engine_path: Path | None = None
    resource: str = "all"
    modules: frozenset[str] = frozenset()
    refresh: bool = False
    offline: bool = False
    pull: bool = False
    push: bool = False
    dry_run: bool = False
    commentaries_repo: str = "git@github.com:getbible/commentaries.git"
    dictionaries_repo: str = "git@github.com:getbible/dictionaries.git"
    commentaries_branch: str = "main"
    dictionaries_branch: str = "main"
    max_document_bytes: int = DOCUMENT_CEILING_BYTES


class BuildPipeline:
    def __init__(self, config: PipelineConfig, http: HttpClient | None = None) -> None:
        self.config = config
        self.http = http or HttpClient()
        self._stage = "configuration"
        self._resource: ResourceKind | None = None
        self._references: dict[tuple[str, str, str], ReferenceEngine] = {}
        report = BuildReport(utc_now(), config.resource, CATALOG_URL)
        try:
            self.policy = ModulePolicy(config.policy_path)
            self.books = BookRegistry(config.books_path)
            self.engine = GetBibleSwordManager(
                config.engine_manifest_path,
                config.work_dir,
                http=self.http,
                offline=config.offline,
            )
            self.bible = BibleApi(
                config.bible_api,
                cache_dir=config.work_dir / "bible",
                http=self.http,
                offline=config.offline,
            )
        except Exception as error:
            self._fatal(report, error)
            raise

    def references_for(self, module: ModuleDescriptor, metadata: dict[str, Any]) -> ReferenceEngine:
        """The reference engine for one module: the Bible in its versification and language."""
        versification = module.first("versification") or str(metadata.get("versification") or "")
        key = (module.name, module.language, versification)
        if key not in self._references:
            self._references[key] = ReferenceEngine.for_module(
                self.bible,
                self.books,
                module.language,
                versification,
                self.config.aliases_dir,
                module_id=slug(module.name),
            )
        return self._references[key]

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
                self.config.work_dir / "repos" / "commentaries",
                self.config.commentaries_branch,
            ),
            "dictionaries": GitRepository(
                self.config.dictionaries_repo,
                self.config.work_dir / "repos" / "dictionaries",
                self.config.dictionaries_branch,
            ),
        }

    def run(self) -> BuildReport:
        report = BuildReport(utc_now(), self.config.resource, CATALOG_URL)
        self._stage = "configuration"
        self._references.clear()
        self._resource = None
        self._write_report(report)
        try:
            return self._run(report)
        except Exception as error:
            self._fatal(report, error)
            raise

    def _run(self, report: BuildReport) -> BuildReport:
        if self.config.offline and self.config.refresh:
            raise ValueError("--offline and --refresh cannot be combined")
        if self.config.push and self.config.modules:
            raise ValueError(
                "Publishing a partial --module build is disabled to protect the public API"
            )
        self._stage = "catalog"
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
        self._write_report(report)
        if not approved:
            raise RuntimeError("The policy did not approve any selected modules")
        identifiers: dict[tuple[ResourceKind, str], str] = {}
        for kind, module in approved:
            module_id = slug(module.name)
            if module_id in RESERVED_MODULE_IDS:
                raise RuntimeError(
                    f"Module {module.name!r} normalizes to the reserved identifier "
                    f"{module_id!r}, which would collide with a generated document"
                )
            key = (kind, module_id)
            if key in identifiers:
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
            report.status = "success"
            report.completed_at = utc_now()
            self._write_report(report)
            return report

        resources = sorted({kind for kind, _ in approved})
        # Schema/configuration errors are global, so find them before module work.
        self._stage = "configuration"
        for kind in resources:
            for name in SCHEMA_NAMES[kind]:
                Draft202012Validator.check_schema(
                    read_json(self.config.schemas_dir / f"{name}.schema.json")
                )
        repositories: dict[ResourceKind, GitRepository] = {}
        previous_roots = {kind: self.config.dist_dir / kind / "v1" for kind in resources}
        if self.config.pull or self.config.push:
            self._stage = "preparation"
            repositories = self._repositories()
            # Prepare every target before using its current v1 as the recovery
            # baseline. A local distribution may be older than the published tree.
            for kind in resources:
                repositories[kind].prepare(self.config.pull)
                previous_roots[kind] = repositories[kind].path / "v1"

        generated_roots: dict[ResourceKind, Path] = {}
        self._stage = "staging"
        for kind in resources:
            path = self.config.work_dir / "generated" / kind / "v1"
            reset_directory(path, boundary=self.config.work_dir / "generated")
            generated_roots[kind] = path

        # Catalog metadata can resolve known versifications before downloading and
        # exporting modules. Keep each module's aliases independent; BibleApi itself
        # caches the shared translation structures.
        preflight_failed: set[tuple[ResourceKind, str]] = set()
        for kind, module in approved:
            if module.first("versification"):
                self._stage = "preflight"
                try:
                    self.references_for(module, {})
                except Exception as error:
                    self._module_failure(report, kind, module, error)
                    preflight_failed.add((kind, module.name))

        self._stage = "engine"
        installer = ModuleInstaller(
            self.config.work_dir / "modules",
            self.http,
            refresh=self.config.refresh,
            offline=self.config.offline,
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
            if (kind, module.name) in preflight_failed:
                continue
            LOG.info("Building %s module %s", kind, module.name)
            module_id = slug(module.name)
            stage = self.config.work_dir / "staging" / kind / module_id / "v1"
            self._stage = "staging"
            reset_directory(stage, boundary=self.config.work_dir / "staging")
            try:
                record, metadata = self._build_module(
                    report, kind, module, stage, installer, exporter
                )
            except Exception as error:
                self._module_failure(report, kind, module, error)
                # A writer can have emitted files before detecting a bad entry or
                # an oversized composed document. None of them reach generated/.
                shutil.rmtree(stage)
                continue
            # Promotion is infrastructure work. A disk/copy failure must not leave
            # a half-promoted module eligible for publication.
            self._stage = "staging"
            self._promote_module(stage, generated_roots[kind], module_id)
            summaries[kind].append(record)
            report.built[kind].append(module.name)
            if metadata.get("storage"):
                report.storage[module.name] = {
                    "resource": kind,
                    "id": record["id"],
                    "entry_count": record["entry_count"],
                    **metadata["storage"],
                }
            self._write_report(report)

        blocked: set[ResourceKind] = set()
        self._stage = "retention"
        for kind in resources:
            failures = [item for item in report.failed if item["resource"] == kind]
            if not failures:
                continue
            try:
                previous = PreviousTree(
                    previous_roots[kind], kind, self.config.schemas_dir, RESERVED_MODULE_IDS
                )
                for failure in failures:
                    module_id = slug(failure["module"])
                    if previous.available(module_id):
                        record = previous.retain(module_id, generated_roots[kind])
                        summaries[kind].append(record)
                        report.retained.append(
                            {
                                "resource": kind,
                                "module": failure["module"],
                                "reason": failure["reason"],
                            }
                        )
                        self._write_report(report)
            except Exception as error:
                # Never drop an old failed module merely because its old manifest
                # is damaged. Preserve that resource unchanged; other resources
                # with safe new output may still advance.
                blocked.add(kind)
                self._error(report, error, resource=kind)

        self._stage = "publication-plan"
        publishable = []
        for kind in resources:
            if not report.built[kind]:
                blocked.add(kind)
                self._error(
                    report,
                    RuntimeError("No module was newly built; this resource remains unchanged"),
                    resource=kind,
                )
            if kind not in blocked:
                publishable.append(kind)
        if not any(report.built.values()):
            raise RuntimeError("No modules built successfully; existing output remains unchanged")
        if not publishable:
            raise RuntimeError(
                "No resource can be replaced safely; existing output remains unchanged"
            )

        report.status = "partial" if report.failed or report.errors else "success"
        report.completed_at = utc_now()
        self._write_report(report)
        generated_at = report.completed_at
        self._stage = "assembly"
        for kind in resources:
            if summaries[kind]:
                self._assemble(generated_roots[kind], kind, summaries[kind], report, generated_at)
        # Every complete generated tree stays available as a workflow artifact,
        # including if a later copy, commit, signing operation, or push fails.
        self._stage = "distribution"
        for kind in publishable:
            replace_tree(generated_roots[kind], self.config.dist_dir / kind / "v1")

        if repositories:
            self._stage = "commit"
            sign = sign_commits_from_environment()
            # Complete every local commit before publishing the first resource.
            for kind in publishable:
                self._resource = kind
                repository = repositories[kind]
                replace_tree(generated_roots[kind], repository.path / "v1")
                report.commits[kind] = repository.commit(
                    f"Build {kind} API v1 ({generated_at})", sign=sign
                )
                self._write_report(report)
            if self.config.push:
                self._stage = "push"
                for kind in publishable:
                    self._resource = kind
                    repositories[kind].push()
                    self._write_report(report)

        report.completed_at = utc_now()
        self._write_report(report)
        return report

    def _build_module(
        self,
        report: BuildReport,
        kind: ResourceKind,
        module: ModuleDescriptor,
        stage: Path,
        installer: ModuleInstaller,
        exporter: SwordExporter,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        exported = None
        try:
            self._stage = "install"
            installation = installer.install(module)
            self._stage = "export"
            exported = exporter.export(installation, module.name)
            self._stage = "classification"
            expected = "commentary" if kind == "commentaries" else "dictionary_or_lexicon"
            if exported.metadata.get("classification") != expected:
                raise RuntimeError(
                    f"getbiblesword classified {module.name} as "
                    f"{exported.metadata.get('classification')!r}, expected {expected!r}"
                )
            if exported.diagnostics:
                report.diagnostics[module.name] = [
                    {
                        "sequence": item.get("sequence"),
                        "severity": item.get("severity"),
                        "code": item.get("code"),
                        "message": self._safe_reason(item.get("message_text", "")),
                    }
                    for item in exported.diagnostics
                ]
            self._stage = "references"
            references = self.references_for(module, exported.metadata)
            self._stage = "write"
            writer_type = CommentaryWriter if kind == "commentaries" else DictionaryWriter
            writer = writer_type(
                stage,
                self.books,
                self.config.schemas_dir,
                self.config.max_document_bytes,
                references=references,
            )
            result = writer.write(module, exported)
            self._stage = "cleanup"
            return result
        finally:
            if exported is not None:
                exported.close()

    @staticmethod
    def _promote_module(stage: Path, destination: Path, module_id: str) -> None:
        expected = {module_id, f"{module_id}.json"}
        if {path.name for path in stage.iterdir()} != expected:
            raise RuntimeError(f"Unexpected staged paths for module {module_id}")
        module_destination = destination / module_id
        whole_destination = destination / f"{module_id}.json"
        if any(
            path.exists() or path.is_symlink() for path in (module_destination, whole_destination)
        ):
            raise RuntimeError(f"Refusing to overwrite promoted module {module_id}")
        os.replace(stage / module_id, module_destination)
        try:
            os.replace(stage / f"{module_id}.json", whole_destination)
        except OSError:
            shutil.rmtree(module_destination)
            raise
        stage.rmdir()

    def _assemble(
        self,
        root: Path,
        kind: ResourceKind,
        summaries: list[dict[str, Any]],
        report: BuildReport,
        generated_at: str,
    ) -> None:
        records = sorted(summaries, key=lambda item: item["id"])
        self._publish_schemas(root, kind)
        prefix = "commentary" if kind == "commentaries" else "dictionary"
        self._write_document(
            root / f"{kind}.json",
            {
                "schema": f"getbible-{kind}-catalog-v1",
                "version": 1,
                "generated_at": generated_at,
                "base_url": BASE_URLS[kind],
                **CATALOG_TEMPLATES[kind],
                "module_count": len(records),
                kind: records,
            },
            f"{prefix}-catalog",
        )
        self._write_document(
            root / "build.json",
            {
                "schema": "getbible-build-v1",
                "builder": "v1_study_builder",
                "builder_version": __version__,
                "extractor": "getbiblesword",
                "extractor_version": self.engine.manifest.version,
                "extractor_contract": self.engine.manifest.contract,
                "api_version": 1,
                "resource": kind,
                "generated_at": generated_at,
                "catalog_url": CATALOG_URL,
                "bible_api": self.config.bible_api,
                "module_count": len(records),
            },
            "build",
        )
        self._write_document(root / "build-report.json", report.as_dict(), "build-report")
        write_json(
            root / "openapi.json",
            openapi_document(kind, [record["id"] for record in records], self.config.schemas_dir),
        )
        # Last: this is the exact set of JSON documents the finished tree owns.
        self._write_document(
            root / "hashes.json",
            {
                "schema": "getbible-hashes-v1",
                "algorithm": "sha256",
                "files": hash_tree(root, exclude={"hashes.json"}),
            },
            "hashes",
        )

    def _safe_reason(self, value: Any) -> str:
        message = str(value)
        for remote in (self.config.commentaries_repo, self.config.dictionaries_repo):
            if remote:
                message = message.replace(remote, "[repository]")
        return message

    def _module_failure(
        self, report: BuildReport, kind: ResourceKind, module: ModuleDescriptor, error: Exception
    ) -> None:
        # Exhausted/broken storage or memory is a build-wide infrastructure problem,
        # not evidence that one module's bytes are unusable.
        if isinstance(error, MemoryError) or (
            isinstance(error, OSError)
            and error.errno
            in {errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO, errno.EMFILE, errno.ENFILE}
        ):
            raise error
        reason = self._safe_reason(error)
        LOG.error(
            "Failed to build %s during %s: %s\n%s",
            module.name,
            self._stage,
            reason,
            self._safe_reason("".join(traceback.format_exception(error))),
        )
        report.failed.append(
            {
                "resource": kind,
                "module": module.name,
                "stage": self._stage,
                "error_type": type(error).__name__,
                "reason": reason,
            }
        )
        self._write_report(report)

    def _error(
        self, report: BuildReport, error: Exception, *, resource: ResourceKind | None = None
    ) -> None:
        item = {
            "stage": self._stage,
            "error_type": type(error).__name__,
            "reason": self._safe_reason(error),
        }
        if resource is not None:
            item["resource"] = resource
        report.errors.append(item)
        LOG.error(
            "%s: %s\n%s",
            self._stage,
            item["reason"],
            self._safe_reason("".join(traceback.format_exception(error))),
        )
        self._write_report(report)

    def _fatal(self, report: BuildReport, error: Exception) -> None:
        report.status = "failed"
        report.completed_at = utc_now()
        self._error(report, error, resource=self._resource)

    def _write_document(self, path: Path, document: dict[str, Any], schema: str) -> None:
        """Write one tree-level document after checking it against its published schema."""
        validate(document, read_json(self.config.schemas_dir / f"{schema}.schema.json"))
        write_json(path, document)

    def _publish_schemas(self, root: Path, kind: ResourceKind) -> None:
        """Publish the schema of every document type beside the data, under schema/."""
        destination = root / "schema"
        destination.mkdir(parents=True, exist_ok=True)
        for name in SCHEMA_NAMES[kind]:
            shutil.copyfile(
                self.config.schemas_dir / f"{name}.schema.json", destination / f"{name}.json"
            )

    def _write_report(self, report: BuildReport) -> None:
        write_json(self.config.work_dir / "reports" / "latest.json", report.as_dict())
