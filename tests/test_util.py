import json

from study_builder.util import hash_tree, replace_tree, write_composed_json, write_json


def test_json_writes_are_stable_and_hashed(tmp_path) -> None:
    root = tmp_path / "api"
    write_json(root / "data.json", {"hello": "world"})
    first = (root / "data.json").read_bytes()
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
    assert composed.read_text(encoding="utf-8").endswith("]\n}\n")


def test_composed_document_handles_an_empty_member_list(tmp_path) -> None:
    composed = tmp_path / "composed.json"
    write_composed_json(composed, {"schema": "test"}, "members", [])
    assert json.loads(composed.read_text(encoding="utf-8")) == {"schema": "test", "members": []}


def test_replace_tree_preserves_only_new_generation(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "target/v1"
    write_json(source / "new.json", {"version": 2})
    write_json(destination / "old.json", {"version": 1})
    replace_tree(source, destination)
    assert json.loads((destination / "new.json").read_text()) == {"version": 2}
    assert not (destination / "old.json").exists()
