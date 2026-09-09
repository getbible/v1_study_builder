# SPDX-License-Identifier: GPL-2.0-only
"""Regression coverage for nonconsecutive Bible API coordinates and their cache."""

import hashlib
import json
from pathlib import Path

import pytest

from study_builder.bible import BibleApi, BibleApiError, Canon, CanonBook
from study_builder.books import BookRegistry
from study_builder.references import ReferenceEngine

# The real finnish1776/71/chapters.json lists these chapter numbers:
# https://github.com/getbible/v2/blob/master/finnish1776/71/chapters.json
# Verse numbers below are a deliberately small synthetic sample, with additional
# gaps and an empty chapter to exercise the coordinate model independently of text.
CHAPTERS = {10: (4, 6, 7), 11: (2, 3), 13: (1, 3, 4), 14: (2, 4), 15: (), 16: (1,)}


def write_translation(tree, abbreviation, chapters, *, versification="Luther"):
    books = [
        {
            "nr": 71,
            "name": "Supplement",
            "chapters": [
                {
                    "chapter": chapter,
                    "verses": [{"verse": verse, "text": "fixture text"} for verse in verses],
                }
                for chapter, verses in chapters.items()
            ],
        }
    ]
    document = {"books": books}
    write_document(tree, abbreviation, document)
    directory = tree / abbreviation
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "books.json").write_text(
        json.dumps({"71": {"nr": 71, "name": "Supplement"}}), encoding="utf-8"
    )
    return {
        "abbreviation": abbreviation,
        "lang": "fi",
        "distribution_versification": versification,
        "sha": (tree / f"{abbreviation}.sha").read_text(encoding="ascii"),
    }


def write_document(tree, abbreviation, document):
    tree.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document).encode("utf-8")
    (tree / f"{abbreviation}.json").write_bytes(payload)
    (tree / f"{abbreviation}.sha").write_text(
        hashlib.sha1(payload, usedforsecurity=False).hexdigest(), encoding="ascii"
    )


@pytest.fixture
def sparse_api(tmp_path):
    tree = tmp_path / "v2"
    translation = write_translation(tree, "finnish1776", CHAPTERS)
    (tree / "translations.json").write_text(
        json.dumps({"finnish1776": translation}), encoding="utf-8"
    )
    return BibleApi(tree, cache_dir=tmp_path / "cache")


def new_engine(api, module_id=None):
    root = Path(__file__).resolve().parents[1]
    return ReferenceEngine.for_module(
        api,
        BookRegistry(root / "conf/book_registry.json"),
        "fi",
        "Luther",
        module_id=module_id,
    )


def coordinates(items):
    return [
        (item["chapter"], item.get("verses", [item["verse"]]) if "verse" in item else None)
        for item in items
    ]


def test_sparse_shape_preserves_membership_and_counts(sparse_api):
    canon = sparse_api.canon_for("fi", "Luther")
    book = canon.books[71]
    assert book.chapter_numbers() == (10, 11, 13, 14, 15, 16)
    assert book.chapter_count == 6
    assert book.verse_count(10) == 3
    assert book.verse_count(12) is None
    assert book.verse_count(15) == 0
    assert canon.has_chapter(71, 10) and not canon.has_chapter(71, 1)
    assert not canon.has_chapter(71, 12)
    assert canon.has_verse(71, 10, 7)
    assert not canon.has_verse(71, 10, 5)
    assert canon.has_chapter(71, 15) and not canon.has_verse(71, 15, 1)
    assert not canon.has_verse(71, 16, True)
    assert not canon.has_verse(71, 10, 4.0)
    assert not canon.has_chapter(71, 10.0)
    assert not canon.has_book(True)
    with pytest.raises(TypeError):
        book.chapters[12] = (1,)


def test_sparse_shape_survives_offline_cache_reload(sparse_api):
    original = sparse_api.canon("finnish1776")
    cache = sparse_api.cache_dir
    offline = BibleApi("https://unreachable.test/v2", cache_dir=cache, offline=True)
    restored = offline.canon("finnish1776")
    assert restored == original
    assert restored[71].chapters == CHAPTERS
    record = json.loads((cache / "shape/finnish1776.json").read_text(encoding="utf-8"))
    assert record["schema"] == "study-builder-bible-canon-v2"
    assert len(record["shape_sha256"]) == 64
    assert "fixture text" not in json.dumps(record)


@pytest.mark.parametrize("module_id", ["sentiment", "varapp"])
def test_the_affected_modules_can_initialize_with_sparse_shape(sparse_api, module_id):
    engine = new_engine(sparse_api, module_id)
    assert coordinates(engine.from_osis("AddEsth.10.4-AddEsth.14.4")) == [
        (10, [4, 6, 7]),
        (11, None),
        (13, None),
        (14, [2, 4]),
    ]
    assert coordinates(engine.from_osis("AddEsth.10-AddEsth.16")) == [
        (10, None),
        (11, None),
        (13, None),
        (14, None),
        (15, None),
        (16, None),
    ]


def test_sparse_verse_ranges_and_lists_never_publish_holes(sparse_api):
    engine = new_engine(sparse_api)
    assert coordinates(engine.from_osis("AddEsth.10.4-AddEsth.10.7")) == [(10, [4, 6, 7])]
    assert engine.from_osis("AddEsth.10.5") == []
    assert engine.from_osis("AddEsth.12.1") == []
    assert engine.from_osis("AddEsth.15.1") == []
    assert coordinates(engine.from_passage("Supplement 10:4,5,6-7")) == [(10, [4, 6, 7])]
    item = engine.from_passage("Supplement 10:4-7")[0]
    assert item["ref"].endswith(" 10:4,6-7")
    assert item["osis"] == "AddEsth.10.4"


def test_ranges_intersect_existing_coordinates_even_when_a_boundary_is_missing(sparse_api):
    engine = new_engine(sparse_api)
    assert coordinates(engine.from_osis("AddEsth.10.5-AddEsth.10.7")) == [(10, [6, 7])]
    assert coordinates(engine.from_osis("AddEsth.12.1-AddEsth.14.4")) == [
        (13, None),
        (14, [2, 4]),
    ]
    assert coordinates(engine.from_osis("AddEsth.10.6-AddEsth.12.1")) == [
        (10, [6, 7]),
        (11, None),
    ]


def test_a_single_sparse_chapter_is_not_reinterpreted_as_chapter_one(sparse_api):
    book = CanonBook(71, "Supplement", {10: (4, 7)}, "finnish1776")
    canon = Canon(
        {71: book},
        {},
        translations=("finnish1776",),
        names_translation=None,
        language="fi",
        versification="Luther",
    )
    registry = new_engine(sparse_api).registry
    engine = ReferenceEngine(canon, registry)
    assert engine.from_passage("Supplement 7") == []
    assert coordinates(engine.from_passage("Supplement 10:7")) == [(10, [7])]


def test_name_probes_use_an_actual_chapter(sparse_api, monkeypatch):
    resolver = new_engine(sparse_api).resolver
    original = resolver.checker.ref
    queries = []

    def checked(reference, translation):
        queries.append(reference)
        return original(reference, translation)

    monkeypatch.setattr(resolver.checker, "ref", checked)
    resolver.name(71)
    assert queries
    assert all(reference.endswith(" 10") for reference in queries)


def test_companion_translations_union_only_coordinates_that_exist(sparse_api):
    tree = Path(sparse_api.location)
    first = write_translation(tree, "finnish1776", {10: (4, 7)})
    second = write_translation(tree, "companion", {10: (6, 7), 13: (2,)}, versification="LutherA")
    (tree / "translations.json").write_text(
        json.dumps({"finnish1776": first, "companion": second}), encoding="utf-8"
    )
    canon = sparse_api.canon_for("fi", "Luther")
    assert canon.chapter_numbers(71) == (10, 13)
    assert canon.verse_numbers(71, 10) == (4, 6, 7)
    assert canon.verse_numbers(71, 13) == (2,)
    assert not canon.has_verse(71, 10, 5)
    assert not canon.has_chapter(71, 12)
    assert canon.translations == ("finnish1776", "companion")


@pytest.mark.parametrize("coordinate", [None, True, False, 0, -1, 301, 10.0, "10"])
def test_invalid_source_chapters_are_refused(sparse_api, coordinate):
    tree = Path(sparse_api.location)
    document = json.loads((tree / "finnish1776.json").read_text(encoding="utf-8"))
    document["books"][0]["chapters"][0]["chapter"] = coordinate
    write_document(tree, "finnish1776", document)
    with pytest.raises(BibleApiError, match="chapter"):
        sparse_api.canon("finnish1776")


@pytest.mark.parametrize("coordinate", [None, True, False, 0, -1, 501, 4.0, "4"])
def test_invalid_source_verses_are_refused(sparse_api, coordinate):
    tree = Path(sparse_api.location)
    document = json.loads((tree / "finnish1776.json").read_text(encoding="utf-8"))
    document["books"][0]["chapters"][0]["verses"][0]["verse"] = coordinate
    write_document(tree, "finnish1776", document)
    with pytest.raises(BibleApiError, match="verse number"):
        sparse_api.canon("finnish1776")


@pytest.mark.parametrize("duplicate", ["chapter", "verse"])
def test_duplicate_source_coordinates_are_refused(sparse_api, duplicate):
    tree = Path(sparse_api.location)
    document = json.loads((tree / "finnish1776.json").read_text(encoding="utf-8"))
    chapters = document["books"][0]["chapters"]
    values = chapters if duplicate == "chapter" else chapters[0]["verses"]
    values.append(values[0])
    write_document(tree, "finnish1776", document)
    with pytest.raises(BibleApiError, match=f"repeats {duplicate}"):
        sparse_api.canon("finnish1776")


def test_unordered_source_coordinates_are_sorted_without_renumbering(sparse_api):
    tree = Path(sparse_api.location)
    document = json.loads((tree / "finnish1776.json").read_text(encoding="utf-8"))
    chapters = document["books"][0]["chapters"]
    chapters.reverse()
    for chapter in chapters:
        chapter["verses"].reverse()
    write_document(tree, "finnish1776", document)
    assert sparse_api.canon("finnish1776")[71].chapters == CHAPTERS


@pytest.mark.parametrize("damage", ["old_schema", "changed_membership", "duplicate", "boolean"])
def test_invalid_sparse_caches_are_rebuilt_online_and_rejected_offline(sparse_api, damage):
    sparse_api.canon("finnish1776")
    cache = sparse_api.cache_dir / "shape/finnish1776.json"
    record = json.loads(cache.read_text(encoding="utf-8"))
    if damage == "old_schema":
        record["schema"] = "study-builder-bible-canon-v1"
    elif damage == "changed_membership":
        record["books"][0]["chapters"][0]["verses"] = [4, 5, 6, 7]
    else:
        chapters = record["books"][0]["chapters"]
        if damage == "duplicate":
            chapters.append(chapters[0])
        else:
            chapters[0]["chapter"] = True
        # Structural validation must hold even when the digest is internally consistent.
        payload = json.dumps(
            record["books"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        record["shape_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    cache.write_text(json.dumps(record), encoding="utf-8")
    offline = BibleApi(sparse_api.location, cache_dir=sparse_api.cache_dir, offline=True)
    with pytest.raises(BibleApiError, match="Offline"):
        offline.canon("finnish1776")
    online = BibleApi(sparse_api.location, cache_dir=sparse_api.cache_dir)
    assert online.canon("finnish1776")[71].chapters == CHAPTERS


def test_sparse_source_hash_is_still_verified(sparse_api):
    tree = Path(sparse_api.location)
    (tree / "finnish1776.sha").write_text("f" * 40, encoding="ascii")
    with pytest.raises(BibleApiError, match="published hash"):
        sparse_api.canon("finnish1776")
