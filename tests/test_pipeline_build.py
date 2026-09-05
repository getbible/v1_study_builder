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
