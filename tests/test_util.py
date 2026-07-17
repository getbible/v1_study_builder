import json

from study_builder.util import replace_tree, write_hash_sidecars, write_json


def test_json_writes_are_stable_and_hashed(tmp_path) -> None:
    root = tmp_path / "api"
    write_json(root / "data.json", {"hello": "world"})
    first = (root / "data.json").read_bytes()
    write_json(root / "data.json", {"hello": "world"})
    assert (root / "data.json").read_bytes() == first
    hashes = write_hash_sidecars(root)
    assert len(hashes["data.json"]) == 64
    assert len((root / "data.json.sha").read_text().strip()) == 40


def test_replace_tree_preserves_only_new_generation(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "target/v1"
    write_json(source / "new.json", {"version": 2})
    write_json(destination / "old.json", {"version": 1})
    replace_tree(source, destination)
    assert json.loads((destination / "new.json").read_text()) == {"version": 2}
    assert not (destination / "old.json").exists()
