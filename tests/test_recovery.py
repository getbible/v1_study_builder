# SPDX-License-Identifier: GPL-2.0-only
from dataclasses import replace

import pytest

from study_builder.books import BookRegistry
from study_builder.commentaries import CommentaryWriter
from study_builder.dictionaries import DictionaryWriter
from study_builder.models import NativeExport
from study_builder.pipeline import BASE_URLS, CATALOG_TEMPLATES, RESERVED_MODULE_IDS
from study_builder.recovery import PreviousTree, RecoveryError
from study_builder.util import hash_tree, read_json, write_json


def _manifest(root):
    write_json(
        root / "hashes.json",
        {
            "schema": "getbible-hashes-v1",
            "algorithm": "sha256",
            "files": hash_tree(root, exclude={"hashes.json"}),
        },
    )


@pytest.fixture(params=["commentaries", "dictionaries"])
def previous(request, tmp_path, project_root, commentary_module, reference_engine):
    kind = request.param
    root = tmp_path / "previous"
    schemas = project_root / "schemas"
    writer_type = CommentaryWriter if kind == "commentaries" else DictionaryWriter
    writer = writer_type(
        root,
        BookRegistry(project_root / "conf/book_registry.json"),
        schemas,
        references=reference_engine,
    )
    entries = [
        {
            "key": key,
            "raw": text,
            "plain": text,
            "html": "",
            "verse": {
                "osis": f"Gen.{chapter}.1",
                "testament": 1,
                "book": 1,
                "chapter": chapter,
                "verse": 1,
            },
        }
        for key, chapter, text in (("Alpha", 0, "Introduction."), ("Beta", 1, "Comment."))
    ]
    records = []
    for name in ("First", "Second"):
        record, _ = writer.write(replace(commentary_module, name=name), NativeExport({}, entries))
        records.append(record)
    write_json(
        root / f"{kind}.json",
        {
            "schema": f"getbible-{kind}-catalog-v1",
            "version": 1,
            "generated_at": "2026-09-09T00:00:00Z",
            "base_url": BASE_URLS[kind],
            **CATALOG_TEMPLATES[kind],
            "module_count": len(records),
            kind: records,
        },
    )
    _manifest(root)
    return root, kind, schemas


def _open(previous):
    root, kind, schemas = previous
    return PreviousTree(root, kind, schemas, RESERVED_MODULE_IDS)


def test_absent_tree_is_distinct_from_incomplete_tree(tmp_path, project_root):
    root = tmp_path / "absent"
    tree = PreviousTree(root, "dictionaries", project_root / "schemas", RESERVED_MODULE_IDS)
    assert tree.records == {}
    assert not tree.available("missing")
    root.mkdir()
    with pytest.raises(RecoveryError, match="Cannot read previous output hashes.json"):
        PreviousTree(root, "dictionaries", project_root / "schemas", RESERVED_MODULE_IDS)


def test_retains_exact_module_bytes_without_previous_root_documents(previous, tmp_path):
    root, kind, _ = previous
    tree = _open(previous)
    destination = tmp_path / "generated"
    record = tree.retain("first", destination)
    assert record == tree.records["first"]
    assert tree.available("first")
    assert not tree.available("unknown")
    expected = {
        name: digest
        for name, digest in hash_tree(root).items()
        if name.startswith("first/") or name == "first.json"
    }
    assert hash_tree(destination) == expected
    assert not (destination / f"{kind}.json").exists()
    assert not list(destination.glob(".retain-*"))


@pytest.mark.parametrize("change", ["corrupt", "missing", "extra"])
def test_bad_module_does_not_prevent_retaining_another(previous, tmp_path, change):
    root, _, _ = previous
    if change == "corrupt":
        (root / "second.json").write_text("corrupt", encoding="utf-8")
    elif change == "missing":
        (root / "second.json").unlink()
    else:
        (root / "second" / "unexpected.json").write_text("{}", encoding="utf-8")
    tree = _open(previous)
    destination = tmp_path / "generated"
    tree.retain("first", destination)
    with pytest.raises(RecoveryError):
        tree.retain("second", destination)
    assert not (destination / "second").exists()
    assert not (destination / "second.json").exists()
    assert not list(destination.glob(".retain-*"))


@pytest.mark.parametrize(
    "unsafe",
    ["../outside.json", "/outside.json", "first/../outside.json", "first//entry.json"],
)
def test_rejects_noncanonical_manifest_paths(previous, unsafe):
    root, _, _ = previous
    manifest = read_json(root / "hashes.json")
    manifest["files"][unsafe] = "0" * 64
    write_json(root / "hashes.json", manifest)
    with pytest.raises(RecoveryError, match="Unsafe previous manifest path"):
        _open(previous)


@pytest.mark.parametrize("unsafe", ["../outside", "First", "hashes", "schema", ""])
def test_rejects_unsafe_catalog_ids(previous, unsafe):
    root, kind, _ = previous
    catalog = read_json(root / f"{kind}.json")
    catalog[kind][0]["id"] = unsafe
    write_json(root / f"{kind}.json", catalog)
    _manifest(root)
    with pytest.raises(RecoveryError):
        _open(previous)


def test_rejects_duplicate_catalog_records(previous):
    root, kind, _ = previous
    catalog = read_json(root / f"{kind}.json")
    catalog[kind].append(catalog[kind][0])
    catalog["module_count"] += 1
    write_json(root / f"{kind}.json", catalog)
    _manifest(root)
    with pytest.raises(RecoveryError, match="Duplicate previous catalog module"):
        _open(previous)


def test_rejects_invalid_or_unverified_catalog(previous):
    root, kind, _ = previous
    catalog = read_json(root / f"{kind}.json")
    catalog["module_count"] = 999
    write_json(root / f"{kind}.json", catalog)
    with pytest.raises(RecoveryError, match="SHA-256 mismatch"):
        _open(previous)
    _manifest(root)
    with pytest.raises(RecoveryError, match="module_count"):
        _open(previous)


@pytest.mark.parametrize("directory", [False, True])
def test_rejects_file_and_directory_symlinks(previous, tmp_path, directory):
    root, _, _ = previous
    target = tmp_path / "outside"
    if directory:
        target.mkdir()
    else:
        target.write_text("{}", encoding="utf-8")
    (root / "linked").symlink_to(target, target_is_directory=directory)
    with pytest.raises(RecoveryError, match="Unsafe previous output"):
        _open(previous)


def test_rejects_duplicate_manifest_json_members(previous):
    root, _, _ = previous
    (root / "hashes.json").write_text(
        '{"schema":"getbible-hashes-v1","algorithm":"sha256","files":{},"files":{}}',
        encoding="utf-8",
    )
    with pytest.raises(RecoveryError, match="Duplicate JSON member"):
        _open(previous)


def test_requires_complete_module_even_if_manifest_omits_missing_whole(previous, tmp_path):
    root, _, _ = previous
    (root / "first.json").unlink()
    _manifest(root)
    with pytest.raises(RecoveryError, match="module is incomplete"):
        _open(previous).retain("first", tmp_path / "generated")


def test_rejects_catalog_metadata_disagreement(previous, tmp_path):
    root, kind, _ = previous
    catalog = read_json(root / f"{kind}.json")
    catalog[kind][0]["name"] = "Different name"
    write_json(root / f"{kind}.json", catalog)
    _manifest(root)
    destination = tmp_path / "generated"
    with pytest.raises(RecoveryError, match="catalog and metadata disagree"):
        _open(previous).retain("first", destination)
    assert not (destination / "first").exists()


def test_refuses_to_overwrite_existing_staged_output(previous, tmp_path):
    destination = tmp_path / "generated"
    destination.mkdir()
    (destination / "first.json").write_text("keep", encoding="utf-8")
    with pytest.raises(RecoveryError, match="Refusing to overwrite"):
        _open(previous).retain("first", destination)
    assert (destination / "first.json").read_text(encoding="utf-8") == "keep"


def test_detects_source_changes_after_opening(previous, tmp_path):
    tree = _open(previous)
    root, _, _ = previous
    (root / "first.json").write_text("changed", encoding="utf-8")
    with pytest.raises(RecoveryError, match="SHA-256 mismatch"):
        tree.retain("first", tmp_path / "generated")


def test_orphan_module_cannot_be_mistaken_for_a_new_module(previous):
    root, kind, _ = previous
    catalog = read_json(root / f"{kind}.json")
    catalog[kind] = catalog[kind][:1]
    catalog["module_count"] = 1
    write_json(root / f"{kind}.json", catalog)
    _manifest(root)
    with pytest.raises(RecoveryError, match="no catalog owner"):
        _open(previous)


@pytest.mark.parametrize("part", ["entry", "book"])
def test_rejects_linked_file_removed_from_tree_and_manifest(previous, tmp_path, part):
    root, kind, _ = previous
    if kind == "dictionaries":
        missing = root / "first" / "k-Alpha.json"
    elif part == "book":
        missing = root / "first" / "1.json"
    else:
        missing = root / "first" / "1" / "0.json"
    missing.unlink()
    _manifest(root)
    destination = tmp_path / "generated"
    with pytest.raises(RecoveryError, match="index links do not match"):
        _open(previous).retain("first", destination)
    assert not (destination / "first").exists()
    _open(previous).retain("second", destination)


def test_rejects_schema_invalid_previous_index(previous, tmp_path):
    root, kind, _ = previous
    index_name = "books" if kind == "commentaries" else "index"
    write_json(root / "first" / f"{index_name}.json", {})
    _manifest(root)
    with pytest.raises(RecoveryError, match=f"Invalid previous {index_name}.json"):
        _open(previous).retain("first", tmp_path / "generated")


def test_rejects_duplicate_previous_index_paths(previous, tmp_path):
    root, kind, _ = previous
    index_name = "books" if kind == "commentaries" else "index"
    path = root / "first" / f"{index_name}.json"
    index = read_json(path)
    if kind == "commentaries":
        index["books"][0]["chapters"].append(0)
    else:
        index["entries"].append(index["entries"][0])
    write_json(path, index)
    _manifest(root)
    with pytest.raises(RecoveryError, match="[Dd]uplicate"):
        _open(previous).retain("first", tmp_path / "generated")


def test_rejects_previous_index_catalog_count_disagreement(previous, tmp_path):
    root, kind, _ = previous
    index_name = "books" if kind == "commentaries" else "index"
    path = root / "first" / f"{index_name}.json"
    index = read_json(path)
    if kind == "commentaries":
        index["books"][0]["entry_count"] += 1
    else:
        index["entry_count"] += 1
    write_json(path, index)
    _manifest(root)
    with pytest.raises(RecoveryError, match="counts disagree"):
        _open(previous).retain("first", tmp_path / "generated")
