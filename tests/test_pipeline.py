import pytest

from study_builder.pipeline import (
    BASE_URLS,
    CATALOG_TEMPLATES,
    RESERVED_MODULE_IDS,
    BuildPipeline,
    PipelineConfig,
)


@pytest.fixture
def pipeline(tmp_path, project_root) -> BuildPipeline:
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
        "commentary-chapter.json",
    }

    dictionary_root = tmp_path / "generated-dictionaries"
    pipeline._publish_schemas(dictionary_root, "dictionaries")
    published = {path.name for path in (dictionary_root / "schema").glob("*.json")}
    assert published == {"dictionary.json", "dictionary-entry.json", "dictionary-index.json"}


def test_published_schema_ids_match_their_served_paths(pipeline, tmp_path) -> None:
    from study_builder.util import read_json

    for kind in ("commentaries", "dictionaries"):
        root = tmp_path / kind
        pipeline._publish_schemas(root, kind)
        for path in sorted((root / "schema").glob("*.json")):
            expected = f"{BASE_URLS[kind].removesuffix('v1/')}schema/v1/{path.name}"
            assert read_json(path)["$id"] == expected
