# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from study_builder.openapi import SCHEMA_NAMES, openapi_document


@pytest.fixture
def schemas_dir(project_root: Path) -> Path:
    return project_root / "schemas"


def _references(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        for value in node.values():
            found.extend(_references(value))
        return found
    if isinstance(node, list):
        return [ref for value in node for ref in _references(value)]
    return []


def _resolve(document: dict[str, Any], pointer: str) -> Any:
    assert pointer.startswith("#/"), pointer
    node: Any = document
    for token in pointer[2:].split("/"):
        node = node[token]
    return node


@pytest.mark.parametrize("kind", ["commentaries", "dictionaries"])
def test_the_description_names_no_host_and_starts_at_v1(schemas_dir: Path, kind: str) -> None:
    document = openapi_document(kind, ["clarke", "easton"], schemas_dir)
    assert document["openapi"] == "3.1.0"
    assert "servers" not in document
    assert document["info"]["version"] == "1"
    text = json.dumps(document)
    assert "http://" not in text and "https://" not in text
    assert all(path.startswith("/v1/") for path in document["paths"])
    for operation in document["paths"].values():
        assert set(operation) == {"get"}
        assert set(operation["get"]["responses"]) == {"200", "404"}


def test_every_document_of_the_tree_is_described(schemas_dir: Path) -> None:
    commentaries = openapi_document("commentaries", ["clarke"], schemas_dir)
    assert list(commentaries["paths"]) == [
        "/v1/commentaries.json",
        "/v1/build.json",
        "/v1/hashes.json",
        "/v1/openapi.json",
        "/v1/schema/{document}.json",
        "/v1/{commentary}/metadata.json",
        "/v1/{commentary}/books.json",
        "/v1/{commentary}/{book}/{chapter}.json",
        "/v1/{commentary}/{book}.json",
        "/v1/{commentary}.json",
    ]
    dictionaries = openapi_document("dictionaries", ["easton"], schemas_dir)
    assert list(dictionaries["paths"]) == [
        "/v1/dictionaries.json",
        "/v1/build.json",
        "/v1/hashes.json",
        "/v1/openapi.json",
        "/v1/schema/{document}.json",
        "/v1/{dictionary}/metadata.json",
        "/v1/{dictionary}/index.json",
        "/v1/{dictionary}/{entry}.json",
        "/v1/{dictionary}.json",
    ]
    chapter = commentaries["paths"]["/v1/{commentary}/{book}/{chapter}.json"]["get"]
    assert [item["name"] for item in chapter["parameters"]] == ["commentary", "book", "chapter"]
    assert chapter["parameters"][0]["schema"]["enum"] == ["clarke"]
    assert chapter["parameters"][1]["schema"] == {"type": "integer", "minimum": 1, "maximum": 83}
    assert chapter["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/commentary-chapter"
    }
    schema = commentaries["paths"]["/v1/schema/{document}.json"]["get"]
    assert schema["parameters"][0]["schema"]["enum"] == list(SCHEMA_NAMES["commentaries"])


@pytest.mark.parametrize("kind", ["commentaries", "dictionaries"])
def test_the_published_schemas_are_embedded_and_every_reference_resolves_inside(
    schemas_dir: Path, kind: str
) -> None:
    document = openapi_document(kind, [], schemas_dir)
    components = document["components"]["schemas"]
    assert list(components) == list(SCHEMA_NAMES[kind])
    for name, schema in components.items():
        assert "$id" not in schema and "$schema" not in schema
        published = json.loads((schemas_dir / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert schema["title"] == published["title"]
        assert schema["required"] == published["required"]
    for reference in _references(document):
        _resolve(document, reference)
    # The whole-commentary schema reaches the chapter entry through two embedded levels.
    if kind == "commentaries":
        items = _resolve(document, "#/components/schemas/commentary/properties/books/items")
        assert items == {"$ref": "#/components/schemas/commentary-book"}
        entry = _resolve(document, "#/components/schemas/commentary-chapter/$defs/entry")
        assert entry["required"] == ["book", "chapter", "verse", "text"]
    # A module parameter without modules to list carries no enum.
    parameter = "commentary" if kind == "commentaries" else "dictionary"
    metadata = document["paths"][f"/v1/{{{parameter}}}/metadata.json"]["get"]
    assert "enum" not in metadata["parameters"][0]["schema"]


def test_a_schema_that_refers_outside_the_tree_is_refused(tmp_path: Path) -> None:
    for name in SCHEMA_NAMES["dictionaries"]:
        (tmp_path / f"{name}.schema.json").write_text(
            json.dumps({"title": name, "type": "object", "required": []}), encoding="utf-8"
        )
    (tmp_path / "dictionary.schema.json").write_text(
        json.dumps(
            {
                "title": "x",
                "type": "object",
                "required": [],
                "properties": {"entries": {"$ref": "commentary-chapter.json"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not publish"):
        openapi_document("dictionaries", [], tmp_path)
