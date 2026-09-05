import json

import pytest

from study_builder.util import hash_tree, replace_tree, write_composed_json, write_json


def test_json_writes_are_stable_and_hashed(tmp_path) -> None:
    root = tmp_path / "api"
    write_json(root / "data.json", {"hello": "world"})
    first = (root / "data.json").read_bytes()
    assert first == b'{"hello":"world"}\n'
    write_json(root / "data.json", {"hello": "world"})
    assert (root / "data.json").read_bytes() == first
    hashes = hash_tree(root)
    assert len(hashes["data.json"]) == 64
    assert not list(root.glob("*.sha"))


def test_hash_tree_can_exclude_its_own_manifest(tmp_path) -> None:
    write_json(tmp_path / "data.json", {"hello": "world"})
    write_json(tmp_path / "hashes.json", {"files": {}})
    assert set(hash_tree(tmp_path, exclude={"hashes.json"})) == {"data.json"}


def test_composed_document_embeds_members_verbatim(tmp_path) -> None:
    parts = []
    for index in (1, 2):
        path = tmp_path / f"part{index}.json"
        write_json(path, {"index": index, "nested": {"values": [index, index + 1]}})
        parts.append(path)
    composed = tmp_path / "composed.json"
    write_composed_json(composed, {"schema": "test", "name": "all"}, "members", parts)

    document = json.loads(composed.read_text(encoding="utf-8"))
    assert document["schema"] == "test"
    assert document["members"] == [json.loads(path.read_text(encoding="utf-8")) for path in parts]
    assert composed.read_text(encoding="utf-8").endswith("]}\n")
    for path in parts:
        assert path.read_bytes() in composed.read_bytes()


def test_composed_document_handles_an_empty_member_list(tmp_path) -> None:
    composed = tmp_path / "composed.json"
    write_composed_json(composed, {"schema": "test"}, "members", [])
    assert json.loads(composed.read_text(encoding="utf-8")) == {"schema": "test", "members": []}


def test_nested_composition_does_not_multiply_reference_padding(tmp_path) -> None:
    chapter = tmp_path / "chapter.json"
    value = {
        "text": 'Unicode λόγος and literal whitespace:  \n\t"quoted"',
        "references": [
            {"ref": "John 1:1", "osis": "John.1.1", "book": 43, "chapter": 1, "verse": 1}
            for _ in range(1000)
        ],
    }
    write_json(chapter, value)
    book = tmp_path / "book.json"
    complete = tmp_path / "complete.json"
    write_composed_json(book, {}, "chapters", [chapter])
    write_composed_json(complete, {}, "books", [book])
    assert json.loads(complete.read_bytes()) == {"books": [{"chapters": [value]}]}
    assert chapter.read_bytes() in book.read_bytes()
    assert book.read_bytes() in complete.read_bytes()
    assert complete.stat().st_size < chapter.stat().st_size + 50
    assert complete.stat().st_size < len(json.dumps(value, indent=2).encode("utf-8"))


def test_failed_composition_preserves_existing_document_and_cleans_temporary(tmp_path) -> None:
    path = tmp_path / "complete.json"
    write_json(path, {"previous": True})
    previous = path.read_bytes()
    with pytest.raises(FileNotFoundError):
        write_composed_json(path, {}, "parts", [tmp_path / "missing.json"])
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".complete.json.*"))


def test_composed_member_name_is_json_escaped(tmp_path) -> None:
    path = tmp_path / "complete.json"
    write_composed_json(path, {}, 'a"b\\c', [])
    assert json.loads(path.read_bytes()) == {'a"b\\c': []}


def test_replace_tree_preserves_only_new_generation(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "target/v1"
    write_json(source / "new.json", {"version": 2})
    write_json(destination / "old.json", {"version": 1})
    replace_tree(source, destination)
    assert json.loads((destination / "new.json").read_text()) == {"version": 2}
    assert not (destination / "old.json").exists()
