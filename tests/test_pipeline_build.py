"""Exercise the whole publication assembly without CrossWire or the extractor."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from study_builder import pipeline as pipeline_module
from study_builder.models import ModuleDescriptor, NativeExport
from study_builder.pipeline import BuildPipeline, PipelineConfig


def descriptor(name: str, driver: str, category: str, **extra: str) -> ModuleDescriptor:
    fields = {
        "description": (f"{name} Description",),
        "lang": ("en",),
        "moddrv": (driver,),
        "category": (category,),
        "distributionlicense": ("Public Domain",),
        "version": ("1.0",),
    }
    fields.update({key: (value,) for key, value in extra.items()})
    return ModuleDescriptor(name=name, fields=fields, conf_path=f"mods.d/{name.lower()}.conf")


COMMENTARY = descriptor("Clarke", "zCom", "Commentaries")
DICTIONARY = descriptor("Easton", "RawLD", "Lexicons / Dictionaries")

EXPORTS: dict[str, NativeExport] = {
    "Clarke": NativeExport(
        metadata={"classification": "commentary"},
        entries=[
            {
                "key": "Dan.0.0",
                "raw": "Introduction to Daniel.",
                "plain": "Introduction to Daniel.",
                "html": "",
                "verse": {"osis": "Dan.0.0", "testament": 1, "book": 27, "chapter": 0, "verse": 0},
            },
            {
                "key": "Dan.1.1",
                "raw": "In the third year.",
                "plain": "In the third year.",
                "html": "<p>In the third year.</p>",
                "verse": {"osis": "Dan.1.1", "testament": 1, "book": 27, "chapter": 1, "verse": 1},
            },
        ],
    ),
    "Easton": NativeExport(
        metadata={"classification": "dictionary_or_lexicon"},
        entries=[
            {
                "key": "KADESH",
                "raw": 'see <a href="sword://Easton/ZIN">ZIN</a> and Num.20.1',
                "plain": "Holy.",
                "html": "",
            },
            {"key": "ZIN", "raw": "a low palm tree", "plain": "A low palm tree.", "html": ""},
        ],
    ),
}


class StubInstaller:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def install(self, module: ModuleDescriptor) -> Path:
        return Path("/nonexistent") / module.name


class StubExporter:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def export(self, installation: Path, name: str) -> NativeExport:
        return EXPORTS[name]


@pytest.fixture
def configured_pipeline(tmp_path, project_root, monkeypatch, bible_tree):
    monkeypatch.setattr(pipeline_module, "ModuleInstaller", StubInstaller)
    monkeypatch.setattr(pipeline_module, "SwordExporter", StubExporter)
    monkeypatch.setattr(BuildPipeline, "_catalog", lambda self: [COMMENTARY, DICTIONARY])
    monkeypatch.setattr(
        pipeline_module.GetBibleSwordManager, "ensure", lambda self, path=None: Path("/stub")
    )
    config = PipelineConfig(
        root=project_root,
        work_dir=tmp_path / "work",
        dist_dir=tmp_path / "dist",
        policy_path=project_root / "conf/module_policy.json",
        books_path=project_root / "conf/book_registry.json",
        schemas_dir=project_root / "schemas",
        engine_manifest_path=project_root / "conf/getbiblesword.json",
        engine_schema_path=project_root / "schemas/getbiblesword-ndjson-v1.schema.json",
        aliases_dir=project_root / "conf/book_aliases",
        bible_api=str(bible_tree),
    )
    return BuildPipeline(config)


@pytest.fixture
def built(configured_pipeline):
    report = configured_pipeline.run()
    return report, configured_pipeline.config.dist_dir


def test_build_publishes_both_resources_under_v1(built) -> None:
    report, dist = built
    assert report.built == {"commentaries": ["Clarke"], "dictionaries": ["Easton"]}
    assert not report.failed
    assert (dist / "commentaries/v1/clarke.json").is_file()
    assert (dist / "commentaries/v1/clarke/27/0.json").is_file()
    assert (dist / "dictionaries/v1/easton/index.json").is_file()
    assert (dist / "dictionaries/v1/easton/k-KADESH.json").is_file()


def test_catalog_url_templates_resolve_from_the_version_root(built) -> None:
    _, dist = built
    catalog = json.loads((dist / "commentaries/v1/commentaries.json").read_text(encoding="utf-8"))
    assert catalog["base_url"] == "https://commentaries.getbible.net/v1/"
    assert catalog["module_count"] == 1
    record = catalog["commentaries"][0]
    assert record["id"] == "clarke"
    assert "about" not in record and "copyright" not in record

    for template, replacements in (
        (catalog["chapter_url_template"], {"commentary": "clarke", "book": "27", "chapter": "1"}),
        (catalog["book_url_template"], {"commentary": "clarke", "book": "27"}),
        (catalog["commentary_url_template"], {"commentary": "clarke"}),
        (catalog["books_url_template"], {"commentary": "clarke"}),
        (catalog["metadata_url_template"], {"commentary": "clarke"}),
    ):
        relative = template
        for key, value in replacements.items():
            relative = relative.replace("{" + key + "}", value)
        assert (dist / "commentaries/v1" / relative).is_file(), relative


def test_hashes_manifest_covers_every_other_document(built) -> None:
    _, dist = built
    root = dist / "dictionaries/v1"
    manifest = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    assert manifest["algorithm"] == "sha256"
    published = {path.relative_to(root).as_posix() for path in root.rglob("*.json")}
    assert set(manifest["files"]) == published - {"hashes.json"}
    assert all(len(digest) == 64 for digest in manifest["files"].values())


def test_schemas_are_published_beside_the_data(built) -> None:
    _, dist = built
    assert (dist / "commentaries/v1/schema/commentary-chapter.json").is_file()
    assert (dist / "dictionaries/v1/schema/dictionary-entry.json").is_file()
    assert not (dist / "commentaries/v1/schema/dictionary-entry.json").exists()


@pytest.mark.parametrize("kind", ["commentaries", "dictionaries"])
def test_every_tree_level_document_matches_its_published_schema(built, kind) -> None:
    from jsonschema import validate

    _, dist = built
    root = dist / kind / "v1"
    prefix = "commentary" if kind == "commentaries" else "dictionary"
    module = "clarke" if kind == "commentaries" else "easton"
    for document, schema in (
        (f"{kind}.json", f"{prefix}-catalog"),
        ("build.json", "build"),
        ("build-report.json", "build-report"),
        ("hashes.json", "hashes"),
        (f"{module}/metadata.json", f"{prefix}-metadata"),
    ):
        validate(
            json.loads((root / document).read_text(encoding="utf-8")),
            json.loads((root / "schema" / f"{schema}.json").read_text(encoding="utf-8")),
        )


@pytest.mark.parametrize("kind", ["commentaries", "dictionaries"])
def test_each_tree_describes_itself_without_naming_a_host(built, kind) -> None:
    _, dist = built
    root = dist / kind / "v1"
    text = (root / "openapi.json").read_text(encoding="utf-8")
    document = json.loads(text)
    assert document["openapi"] == "3.1.0" and "servers" not in document
    assert "getbible.net" not in text
    module = "clarke" if kind == "commentaries" else "easton"
    parameter = "commentary" if kind == "commentaries" else "dictionary"
    metadata = document["paths"][f"/v1/{{{parameter}}}/metadata.json"]["get"]
    assert metadata["parameters"][0]["schema"]["enum"] == [module]
    # Every schema the description embeds is also published beside the data.
    for name in document["components"]["schemas"]:
        assert (root / "schema" / f"{name}.json").is_file(), name
    # The description is one of the documents hashes.json vouches for.
    manifest = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    assert "openapi.json" in manifest["files"]


def test_no_document_publishes_markup(built) -> None:
    _, dist = built
    for path in dist.rglob("*.json"):
        assert '"html"' not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("failure", ["prepare", "commit"])
def test_all_targets_are_prepared_and_committed_before_any_push(
    configured_pipeline, monkeypatch, failure
) -> None:
    pipeline = configured_pipeline
    pipeline.config = replace(pipeline.config, pull=True, push=True)
    repositories = {}
    for kind in ("commentaries", "dictionaries"):
        repository = Mock()
        repository.path = pipeline.config.work_dir / "repos" / kind
        previous = repository.path / "v1/previous.json"
        previous.parent.mkdir(parents=True)
        previous.write_text('{"previous":true}', encoding="utf-8")
        repository.commit.return_value = "test-commit"
        repositories[kind] = repository
    getattr(repositories["dictionaries"], failure).side_effect = RuntimeError("target failure")
    monkeypatch.setattr(pipeline, "_repositories", lambda: repositories)

    with pytest.raises(RuntimeError, match="target failure"):
        pipeline.run()

    for repository in repositories.values():
        repository.push.assert_not_called()
        if failure == "prepare":
            repository.commit.assert_not_called()
            assert (repository.path / "v1/previous.json").read_text() == '{"previous":true}'


def read_report(pipeline):
    return json.loads(
        (pipeline.config.work_dir / "reports/latest.json").read_text(encoding="utf-8")
    )


def second_dictionary(pipeline, monkeypatch):
    other = descriptor("Other", "RawLD", "Lexicons / Dictionaries")
    monkeypatch.setitem(EXPORTS, "Other", EXPORTS["Easton"])
    monkeypatch.setattr(pipeline, "_catalog", lambda: [COMMENTARY, DICTIONARY, other])
    return other


def fail_export(monkeypatch, *names):
    original = StubExporter.export

    def export(self, installation, name):
        if name in names:
            raise RuntimeError(f"Invalid extractor stream for {name}")
        return original(self, installation, name)

    monkeypatch.setattr(StubExporter, "export", export)


def test_isolated_export_failure_still_publishes_successful_resource(
    configured_pipeline, monkeypatch
):
    pipeline = configured_pipeline
    fail_export(monkeypatch, "Clarke")
    report = pipeline.run()
    assert report.status == "partial"
    assert report.built == {"commentaries": [], "dictionaries": ["Easton"]}
    assert report.failed == [
        {
            "resource": "commentaries",
            "module": "Clarke",
            "stage": "export",
            "error_type": "RuntimeError",
            "reason": "Invalid extractor stream for Clarke",
        }
    ]
    assert not (pipeline.config.dist_dir / "commentaries/v1").exists()
    root = pipeline.config.dist_dir / "dictionaries/v1"
    public = json.loads((root / "build-report.json").read_text())
    assert public["status"] == "partial"
    assert public["failed"] == report.failed
    assert "build-report.json" in json.loads((root / "hashes.json").read_text())["files"]
    assert read_report(pipeline)["status"] == "partial"


def test_failed_module_retains_exact_old_files_beside_a_fresh_module(
    configured_pipeline, monkeypatch
):
    from study_builder.util import hash_tree

    pipeline = configured_pipeline
    second_dictionary(pipeline, monkeypatch)
    pipeline.run()
    root = pipeline.config.dist_dir / "dictionaries/v1"
    previous = hash_tree(root / "easton")
    whole = (root / "easton.json").read_bytes()
    fail_export(monkeypatch, "Easton")
    report = pipeline.run()
    assert report.status == "partial"
    assert report.built["dictionaries"] == ["Other"]
    assert report.retained == [
        {
            "resource": "dictionaries",
            "module": "Easton",
            "reason": "Invalid extractor stream for Easton",
        }
    ]
    assert hash_tree(root / "easton") == previous
    assert (root / "easton.json").read_bytes() == whole
    catalog = json.loads((root / "dictionaries.json").read_text())
    assert catalog["module_count"] == 2
    assert [record["id"] for record in catalog["dictionaries"]] == ["easton", "other"]
    api = json.loads((root / "openapi.json").read_text())
    operation = api["paths"]["/v1/{dictionary}/metadata.json"]["get"]
    assert operation["parameters"][0]["schema"]["enum"] == ["easton", "other"]
    manifest = json.loads((root / "hashes.json").read_text())["files"]
    assert manifest == hash_tree(root, exclude={"hashes.json"})


def test_failed_writer_never_leaks_partial_files(configured_pipeline, monkeypatch):
    pipeline = configured_pipeline
    second_dictionary(pipeline, monkeypatch)
    original = pipeline_module.DictionaryWriter.write

    def write(self, module, exported):
        if module.name == "Easton":
            partial = self.root / "easton/k-PARTIAL.json"
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text('{"unvalidated":true}')
            raise RuntimeError("Failure after writing an entry")
        return original(self, module, exported)

    monkeypatch.setattr(pipeline_module.DictionaryWriter, "write", write)
    report = pipeline.run()
    assert report.status == "partial"
    assert report.failed[0]["stage"] == "write"
    for output in (pipeline.config.dist_dir, pipeline.config.work_dir / "generated"):
        assert not list(output.rglob("*PARTIAL*"))
        assert not list(output.rglob("easton.json"))
    root = pipeline.config.dist_dir / "dictionaries/v1"
    assert (root / "other/index.json").is_file()
    catalog = json.loads((root / "dictionaries.json").read_text())
    assert [record["id"] for record in catalog["dictionaries"]] == ["other"]


def test_failed_resource_with_no_new_modules_is_never_replaced(
    configured_pipeline, monkeypatch
):
    from study_builder.util import hash_tree

    pipeline = configured_pipeline
    pipeline.run()
    root = pipeline.config.dist_dir / "commentaries/v1"
    before = hash_tree(root)
    fail_export(monkeypatch, "Clarke")
    report = pipeline.run()
    assert report.status == "partial"
    assert hash_tree(root) == before
    assert report.retained[0]["module"] == "Clarke"
    assert any(
        error.get("resource") == "commentaries" and error["stage"] == "publication-plan"
        for error in report.errors
    )


def test_bad_prior_catalog_blocks_only_affected_resource(configured_pipeline, monkeypatch):
    from study_builder.util import hash_tree

    pipeline = configured_pipeline
    second_dictionary(pipeline, monkeypatch)
    pipeline.run()
    root = pipeline.config.dist_dir / "dictionaries/v1"
    (root / "dictionaries.json").write_text('{"broken":true}')
    before = hash_tree(root)
    fail_export(monkeypatch, "Easton")
    report = pipeline.run()
    assert report.status == "partial"
    assert report.built == {"commentaries": ["Clarke"], "dictionaries": ["Other"]}
    assert hash_tree(root) == before
    assert any(
        error.get("resource") == "dictionaries" and error["stage"] == "retention"
        for error in report.errors
    )
    assert (pipeline.config.work_dir / "generated/dictionaries/v1/other.json").is_file()
    assert read_report(pipeline)["status"] == "partial"


def test_no_successes_is_fatal_and_preserves_previous_distribution(
    configured_pipeline, monkeypatch
):
    from study_builder.util import hash_tree

    pipeline = configured_pipeline
    pipeline.run()
    before = hash_tree(pipeline.config.dist_dir)
    fail_export(monkeypatch, "Clarke", "Easton")
    with pytest.raises(RuntimeError, match="No modules built successfully"):
        pipeline.run()
    assert hash_tree(pipeline.config.dist_dir) == before
    report = read_report(pipeline)
    assert report["status"] == "failed"
    assert len(report["failed"]) == 2
    assert len(report["retained"]) == 2
    assert report["completed_at"]


def test_catalog_and_constructor_errors_write_durable_report(configured_pipeline, monkeypatch):
    pipeline = configured_pipeline
    monkeypatch.setattr(pipeline, "_catalog", Mock(side_effect=RuntimeError("catalog unavailable")))
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        pipeline.run()
    report = read_report(pipeline)
    assert report["status"] == "failed"
    assert report["errors"][-1]["stage"] == "catalog"

    config = replace(pipeline.config, policy_path=pipeline.config.work_dir / "missing-policy.json")
    with pytest.raises(FileNotFoundError):
        BuildPipeline(config)
    report = read_report(pipeline)
    assert report["status"] == "failed"
    assert report["errors"][-1]["stage"] == "configuration"


def test_preflight_prevents_expensive_export_of_unresolvable_module(
    configured_pipeline, monkeypatch
):
    pipeline = configured_pipeline
    module = descriptor("Easton", "RawLD", "Lexicons / Dictionaries", versification="Broken")
    monkeypatch.setattr(pipeline, "_catalog", lambda: [COMMENTARY, module])
    original = pipeline.references_for

    def references_for(descriptor, metadata):
        if descriptor.name == "Easton":
            raise ValueError("Unknown structure")
        return original(descriptor, metadata)

    monkeypatch.setattr(pipeline, "references_for", references_for)
    installer = Mock(wraps=StubInstaller().install)
    monkeypatch.setattr(StubInstaller, "install", installer)
    report = pipeline.run()
    assert report.status == "partial"
    assert report.failed[0]["stage"] == "preflight"
    assert [call.args[0].name for call in installer.call_args_list] == ["Clarke"]


def test_failed_module_is_retried_on_the_next_build(configured_pipeline, monkeypatch):
    pipeline = configured_pipeline
    original = StubExporter.export
    fail_export(monkeypatch, "Easton")
    assert pipeline.run().status == "partial"
    monkeypatch.setattr(StubExporter, "export", original)
    report = pipeline.run()
    assert report.status == "success"
    assert report.built["dictionaries"] == ["Easton"]
    assert report.failed == []


def test_close_failure_is_isolated_before_module_promotion(configured_pipeline, monkeypatch):
    pipeline = configured_pipeline
    failing = Mock()
    failing.metadata = EXPORTS["Easton"].metadata
    failing.entries = EXPORTS["Easton"].entries
    failing.diagnostics = ()
    failing.close.side_effect = RuntimeError("spool cleanup failed")
    monkeypatch.setitem(EXPORTS, "Easton", failing)
    report = pipeline.run()
    assert report.status == "partial"
    assert report.failed[0]["stage"] == "cleanup"
    assert not (pipeline.config.work_dir / "generated/dictionaries/v1/easton.json").exists()


def test_partial_module_push_stays_forbidden(configured_pipeline):
    pipeline = configured_pipeline
    pipeline.config = replace(pipeline.config, modules=frozenset({"Easton"}), push=True)
    with pytest.raises(ValueError, match="partial --module"):
        pipeline.run()
    assert read_report(pipeline)["status"] == "failed"


def test_disk_exhaustion_is_fatal_with_a_report(configured_pipeline, monkeypatch):
    import errno

    pipeline = configured_pipeline
    monkeypatch.setattr(
        StubInstaller, "install", Mock(side_effect=OSError(errno.ENOSPC, "No space left"))
    )
    with pytest.raises(OSError, match="No space left"):
        pipeline.run()
    report = read_report(pipeline)
    assert report["status"] == "failed"
    assert report["errors"][-1]["stage"] == "install"
    assert report["failed"] == []


def test_prepared_repository_is_the_authoritative_retention_source(
    configured_pipeline, monkeypatch
):
    import shutil

    pipeline = configured_pipeline
    second_dictionary(pipeline, monkeypatch)
    pipeline.run()
    repositories = {}
    for kind in ("commentaries", "dictionaries"):
        repository = Mock()
        repository.path = pipeline.config.work_dir / "repos" / kind
        shutil.copytree(pipeline.config.dist_dir / kind / "v1", repository.path / "v1")
        repository.commit.return_value = "new-commit"
        repositories[kind] = repository
    # An invalid local distribution must not defeat recovery from the prepared
    # target, which is the actual baseline the push would replace.
    (pipeline.config.dist_dir / "dictionaries/v1/dictionaries.json").write_text("corrupt")
    pipeline.config = replace(pipeline.config, pull=True, push=True)
    monkeypatch.setattr(pipeline, "_repositories", lambda: repositories)
    fail_export(monkeypatch, "Easton")
    report = pipeline.run()
    assert report.status == "partial"
    assert report.retained[0]["module"] == "Easton"
    for repository in repositories.values():
        repository.prepare.assert_called_once_with(True)
        repository.push.assert_called_once()


def test_push_failure_keeps_generated_output_and_redacts_repository_in_report(
    configured_pipeline, monkeypatch
):
    pipeline = configured_pipeline
    remote = "https://username:secret@example.invalid/commentaries.git"
    pipeline.config = replace(pipeline.config, pull=True, push=True, commentaries_repo=remote)
    repositories = {}
    for kind in ("commentaries", "dictionaries"):
        repository = Mock()
        repository.path = pipeline.config.work_dir / "repos" / kind
        repository.commit.return_value = f"commit-{kind}"
        repositories[kind] = repository
    repositories["commentaries"].push.side_effect = RuntimeError(f"Unable to push {remote}")
    monkeypatch.setattr(pipeline, "_repositories", lambda: repositories)
    with pytest.raises(RuntimeError, match="Unable to push"):
        pipeline.run()
    for kind in repositories:
        root = pipeline.config.work_dir / "generated" / kind / "v1"
        assert (root / "hashes.json").is_file()
        assert (pipeline.config.dist_dir / kind / "v1/hashes.json").is_file()
        assert json.loads((root / "build-report.json").read_text())["status"] == "success"
    report = read_report(pipeline)
    assert report["status"] == "failed"
    assert report["errors"][-1]["stage"] == "push"
    assert "secret" not in json.dumps(report)
    assert report["commits"] == {
        "commentaries": "commit-commentaries",
        "dictionaries": "commit-dictionaries",
    }


def test_promotion_failure_removes_half_promoted_module(tmp_path, monkeypatch):
    import os

    stage = tmp_path / "staging"
    destination = tmp_path / "generated"
    destination.mkdir()
    (stage / "easton").mkdir(parents=True)
    (stage / "easton/metadata.json").write_text("{}")
    (stage / "easton.json").write_text("{}")
    original = os.replace

    def replace(source, target):
        if source == stage / "easton.json":
            raise OSError("rename failed")
        return original(source, target)

    monkeypatch.setattr(pipeline_module.os, "replace", replace)
    with pytest.raises(OSError, match="rename failed"):
        BuildPipeline._promote_module(stage, destination, "easton")
    assert list(destination.iterdir()) == []
