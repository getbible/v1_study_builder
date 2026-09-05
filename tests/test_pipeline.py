from dataclasses import replace

import pytest

from study_builder.pipeline import (
    BASE_URLS,
    CATALOG_TEMPLATES,
    RESERVED_MODULE_IDS,
    BuildPipeline,
    PipelineConfig,
)


@pytest.fixture
def pipeline(tmp_path, project_root, bible_tree) -> BuildPipeline:
    return BuildPipeline(
        PipelineConfig(
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
    )


def test_reserved_identifiers_cover_every_root_document() -> None:
    # A module directory and its whole-module document share the v1 root with these.
    for name in ("commentaries", "dictionaries", "build", "hashes", "schema"):
        assert name in RESERVED_MODULE_IDS


def test_every_resource_publishes_a_base_url_and_url_templates() -> None:
    for kind in ("commentaries", "dictionaries"):
        assert BASE_URLS[kind].endswith("/v1/")
        assert CATALOG_TEMPLATES[kind]
        for template in CATALOG_TEMPLATES[kind].values():
            assert template.startswith("{" + kind.removesuffix("ies") + "y}")


def test_schemas_are_published_beside_the_data(pipeline, tmp_path) -> None:
    root = tmp_path / "generated"
    pipeline._publish_schemas(root, "commentaries")
    published = {path.name for path in (root / "schema").glob("*.json")}
    assert published == {
        "commentary.json",
        "commentary-book.json",
        "commentary-books.json",
        "commentary-catalog.json",
        "commentary-chapter.json",
        "commentary-metadata.json",
        "build.json",
        "hashes.json",
    }

    dictionary_root = tmp_path / "generated-dictionaries"
    pipeline._publish_schemas(dictionary_root, "dictionaries")
    published = {path.name for path in (dictionary_root / "schema").glob("*.json")}
    assert published == {
        "dictionary.json",
        "dictionary-catalog.json",
        "dictionary-entry.json",
        "dictionary-index.json",
        "dictionary-metadata.json",
        "build.json",
        "hashes.json",
    }


def test_published_schemas_refer_to_each_other_by_published_name(pipeline, tmp_path) -> None:
    """A schema names no host: it refers to a sibling by the file name it is published
    under, which resolves wherever the schema folder is served."""
    from study_builder.util import read_json

    def references(node):
        if isinstance(node, dict):
            yield from ([node["$ref"]] if isinstance(node.get("$ref"), str) else [])
            for value in node.values():
                yield from references(value)
        elif isinstance(node, list):
            for value in node:
                yield from references(value)

    for kind in ("commentaries", "dictionaries"):
        root = tmp_path / kind
        pipeline._publish_schemas(root, kind)
        for path in sorted((root / "schema").glob("*.json")):
            schema = read_json(path)
            assert "$id" not in schema
            for reference in references(schema):
                assert reference.startswith("#/") or (root / "schema" / reference).is_file(), (
                    path.name,
                    reference,
                )


def test_offline_pipeline_configures_engine_and_bible_without_network(pipeline) -> None:
    offline = BuildPipeline(replace(pipeline.config, offline=True))
    assert offline.engine.offline
    assert offline.bible.offline


def test_pipeline_rejects_offline_refresh_before_loading_catalog(pipeline, monkeypatch) -> None:
    pipeline.config = replace(pipeline.config, offline=True, refresh=True)

    def unexpected_catalog():
        pytest.fail("The catalog must not be loaded with contradictory download settings")

    monkeypatch.setattr(pipeline, "_catalog", unexpected_catalog)
    with pytest.raises(ValueError, match="--offline and --refresh cannot be combined"):
        pipeline.run()
