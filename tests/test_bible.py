import hashlib
import json
from pathlib import Path

import pytest

from study_builder.bible import BibleApi, BibleApiError, primary_language
from support.bible_fixture import TRANSLATIONS, write_bible_tree


class CountingHttp:
    """Serve a local tree over the HttpClient interface, counting what is fetched."""

    def __init__(self, tree: Path) -> None:
        self.tree = tree
        self.requests: list[str] = []

    def get_bytes(self, url: str, headers=None) -> bytes:
        relative = url.split("/v2/", 1)[1]
        self.requests.append(relative)
        path = self.tree / relative
        if not path.is_file():
            raise RuntimeError(f"Unable to download {url}: not found")
        return path.read_bytes()


@pytest.fixture
def remote(bible_tree, tmp_path):
    http = CountingHttp(bible_tree)
    api = BibleApi("https://example.test/v2", cache_dir=tmp_path / "cache", http=http)
    return api, http


def test_primary_language_reduces_a_tag_to_its_first_subtag() -> None:
    assert primary_language("zh-Hans") == "zh"
    assert primary_language("EN") == "en"
    assert primary_language("") == "und"


def test_catalogue_lists_language_and_versification(bible_api) -> None:
    catalogue = bible_api.translations()
    assert catalogue["kjv"].language == "en"
    assert catalogue["kjv"].versification == "KJV"
    assert catalogue["vietnamese"].versification == ""
    assert len(catalogue["kjv"].sha) == 40


def test_canon_keeps_only_the_shape_of_a_translation(bible_api) -> None:
    books = bible_api.canon("kjv")
    assert books[1].name == "Genesis"
    assert books[1].chapter_count == 50
    assert books[1].verse_count(1) == 31
    assert books[1].verse_count(51) is None
    assert books[19].verse_count(119) == 176
    assert 73 not in books
    cached = json.loads((bible_api.cache_dir / "shape" / "kjv.json").read_text(encoding="utf-8"))
    assert cached["schema"] == "study-builder-bible-canon-v1"
    assert cached["sha"] == bible_api.translations()["kjv"].sha
    assert "text" not in json.dumps(cached)
    assert cached["books"][0] == {"number": 1, "name": "Genesis", "verses": list(books[1].verses)}


def test_an_unchanged_hash_does_not_download_the_translation_again(remote) -> None:
    api, http = remote
    api.canon("kjv")
    assert "kjv.json" in http.requests
    first = len(http.requests)

    again = BibleApi(api.location, cache_dir=api.cache_dir, http=http)
    again.canon("kjv")
    later = http.requests[first:]
    assert "kjv.sha" in later
    assert "kjv.json" not in later


def test_a_changed_hash_refreshes_the_cached_shape(remote, bible_tree) -> None:
    api, http = remote
    api.canon("klv")
    cache = api.cache_dir / "shape" / "klv.json"
    stale = json.loads(cache.read_text(encoding="utf-8"))
    stale["sha"] = "0" * 40
    stale["books"][0]["verses"] = [1]
    cache.write_text(json.dumps(stale), encoding="utf-8")

    fresh = BibleApi(api.location, cache_dir=api.cache_dir, http=http).canon("klv")
    assert fresh[1].chapter_count == 50


def test_a_translation_that_does_not_match_its_hash_is_refused(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    (tree / "klv.sha").write_text("f" * 40, encoding="ascii")
    with pytest.raises(BibleApiError, match="hash"):
        BibleApi(tree, cache_dir=tmp_path / "cache").canon("klv")


def test_a_translation_that_contradicts_its_books_index_is_refused(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    index = json.loads((tree / "klv/books.json").read_text(encoding="utf-8"))
    index["1"]["name"] = "Elsewhere"
    (tree / "klv/books.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(BibleApiError, match="books index"):
        BibleApi(tree, cache_dir=tmp_path / "cache").canon("klv")


def test_a_local_tree_without_hashes_is_hashed_on_the_spot(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    (tree / "klv.sha").unlink()
    api = BibleApi(tree, cache_dir=tmp_path / "cache")
    assert api.canon("klv")[1].name == "Genesis"
    cached = json.loads((api.cache_dir / "shape" / "klv.json").read_text(encoding="utf-8"))
    expected = hashlib.sha1((tree / "klv.json").read_bytes(), usedforsecurity=False).hexdigest()
    assert cached["sha"] == expected


def test_offline_mode_uses_the_cache_and_nothing_else(bible_tree, tmp_path) -> None:
    online = BibleApi(bible_tree, cache_dir=tmp_path / "cache")
    online.canon("kjv")
    online.book_names("kjv")

    offline = BibleApi("https://unreachable.test/v2", cache_dir=tmp_path / "cache", offline=True)
    assert offline.canon("kjv")[1].chapter_count == 50
    assert offline.book_names("kjv")[1] == "Genesis"
    with pytest.raises(BibleApiError, match="Offline"):
        offline.canon("vulgate")
    with pytest.raises(BibleApiError, match="Offline"):
        empty = BibleApi("https://unreachable.test/v2", cache_dir=tmp_path / "empty", offline=True)
        empty.translations()


def test_a_module_is_matched_by_versification_then_language(bible_api) -> None:
    assert bible_api.select("en", "KJV") == (["kjv", "kjva"], "kjv")
    assert bible_api.select("la", "Vulg") == (["vulgate"], "vulgate")
    # With no French Vulgate, the translation named after the versification gives the
    # shape, ahead of an alphabetically earlier English one.
    assert bible_api.select("fr", "Vulg") == (["vulgate"], None)
    assert bible_api.select("en", "Vulg") == (["douayrheims"], "douayrheims")
    assert bible_api.select("sv", "NRSVA") == (["swedish"], "swedish")
    # No translation declares a Luther versification, so the KJV family gives the shape and
    # the German translation gives the names.
    assert bible_api.select("de", "Luther") == (["kjv", "kjva"], "luther1545")
    # A module without a versification is KJV; Vietnamese names come from Vietnamese.
    assert bible_api.select("vi", "") == (["kjv", "kjva"], "vietnamese")
    assert bible_api.select("tlh", "") == (["kjv", "kjva"], "klv")
    # A language the API has no translation in keeps the shape translation's names.
    assert bible_api.select("xx", "KJV") == (["kjv", "kjva"], None)


def test_the_canon_of_a_module_unites_its_shape_and_names_its_books(bible_api) -> None:
    canon = bible_api.canon_for("en", "KJV")
    assert canon.translations == ("kjv", "kjva")
    assert canon.has_book(1) and canon.has_book(73)
    assert canon.books[73].translation == "kjva"
    assert canon.chapter_count(73) == 19
    assert canon.has_chapter(1, 50) and not canon.has_chapter(1, 51)
    assert canon.has_verse(19, 119, 176) and not canon.has_verse(19, 119, 177)
    assert canon.name(1) == "Genesis"
    assert canon.name(73) == "Wisdom"
    assert canon.describe() == {
        "api": "getbible-v2",
        "versification": "KJV",
        "translations": ["kjv", "kjva"],
        "names": "kjv",
        "language": "en",
    }

    swedish = bible_api.canon_for("sv", "NRSVA")
    assert swedish.name(1) == "Första Moseboken"
    assert not swedish.has_book(19)

    latin = bible_api.canon_for("la", "Vulg")
    assert latin.verse_count(19, 10) == 39
    assert latin.name(19) == "Psalmi"

    vietnamese = bible_api.canon_for("vi", "")
    assert vietnamese.name(43) == "Giăng"
    assert vietnamese.name(1) == "Genesis"
    assert vietnamese.has_book(19)


def test_a_books_index_that_moved_without_the_translation_is_read_afresh(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    cache = tmp_path / "cache"
    BibleApi(tree, cache_dir=cache).canon("klv")
    index = json.loads((tree / "klv/books.json").read_text(encoding="utf-8"))
    index["1"]["name"] = "Other"
    (tree / "klv/books.json").write_text(json.dumps(index), encoding="utf-8")
    # The translation's hash is unchanged, so the cached shape would have been reused;
    # the index no longer agrees with it, so the translation is read again and refused.
    with pytest.raises(BibleApiError, match="books index"):
        BibleApi(tree, cache_dir=cache).canon("klv")


def test_a_corrupt_cache_is_reported_offline_and_replaced_online(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    cache = tmp_path / "cache"
    online = BibleApi(tree, cache_dir=cache)
    online.canon("klv")
    online.book_names("klv")
    (cache / "translations.json").write_text("{corrupt", encoding="utf-8")
    (cache / "books" / "klv.json").write_bytes(b"\xff\xfe")
    shape = cache / "shape" / "klv.json"
    record = json.loads(shape.read_text(encoding="utf-8"))
    record["books"] = []
    shape.write_text(json.dumps(record), encoding="utf-8")

    offline = BibleApi("https://unreachable.test/v2", cache_dir=cache, offline=True)
    with pytest.raises(BibleApiError, match="unreadable"):
        offline.translations()
    with pytest.raises(BibleApiError, match="unreadable"):
        offline.book_names("klv")
    with pytest.raises(BibleApiError, match="Offline"):
        offline.canon("klv")
    assert BibleApi(tree, cache_dir=cache).canon("klv")[1].chapter_count == 50


def test_a_translation_with_an_absurd_verse_number_is_refused(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    document = json.loads((tree / "klv.json").read_text(encoding="utf-8"))
    document["books"][0]["chapters"][0]["verses"].append({"chapter": 1, "verse": 10**30})
    payload = json.dumps(document).encode("utf-8")
    (tree / "klv.json").write_bytes(payload)
    (tree / "klv.sha").write_text(
        hashlib.sha1(payload, usedforsecurity=False).hexdigest(), encoding="ascii"
    )
    with pytest.raises(BibleApiError, match="verse number"):
        BibleApi(tree, cache_dir=tmp_path / "cache").canon("klv")


@pytest.mark.parametrize("number", [None, True, 0, 201, "1"])
def test_an_invalid_cached_book_number_is_rejected_offline_and_refreshed_online(
    tmp_path, number
) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    cache = tmp_path / "cache"
    BibleApi(tree, cache_dir=cache).canon("klv")
    shape = cache / "shape" / "klv.json"
    record = json.loads(shape.read_text(encoding="utf-8"))
    record["books"][0]["number"] = number
    shape.write_text(json.dumps(record), encoding="utf-8")

    offline = BibleApi("https://unreachable.test/v2", cache_dir=cache, offline=True)
    with pytest.raises(BibleApiError, match="Offline"):
        offline.canon("klv")
    assert BibleApi(tree, cache_dir=cache).canon("klv")[1].chapter_count == 50


def test_a_cached_duplicate_book_cannot_override_the_validated_shape(tmp_path) -> None:
    tree = write_bible_tree(tmp_path / "v2", {"klv": TRANSLATIONS["klv"]})
    cache = tmp_path / "cache"
    BibleApi(tree, cache_dir=cache).canon("klv")
    shape = cache / "shape" / "klv.json"
    record = json.loads(shape.read_text(encoding="utf-8"))
    record["books"].append({**record["books"][0], "verses": [1]})
    shape.write_text(json.dumps(record), encoding="utf-8")

    offline = BibleApi("https://unreachable.test/v2", cache_dir=cache, offline=True)
    with pytest.raises(BibleApiError, match="Offline"):
        offline.canon("klv")
    assert BibleApi(tree, cache_dir=cache).canon("klv")[1].chapter_count == 50
