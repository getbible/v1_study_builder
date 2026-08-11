import json

from study_builder.books import BookRegistry
from study_builder.dictionaries import (
    DictionaryWriter,
    canonical_strong,
    encoded_entry_id,
    link_candidates,
    search_key,
)
from study_builder.models import NativeExport


def write(tmp_path, project_root, module, entries, metadata=None):
    writer = DictionaryWriter(
        tmp_path,
        BookRegistry(project_root / "conf/book_registry.json"),
        project_root / "schemas",
    )
    return writer.write(module, NativeExport(metadata=metadata or {}, entries=entries))


def test_strong_keys_match_bible_api_v3() -> None:
    assert canonical_strong("3056", "G") == "G3056"
    assert canonical_strong("00430", "H") == "H0430"
    assert canonical_strong("H07225", None) == "H07225"
    assert canonical_strong("00011", "G") == "G11"
    assert canonical_strong("agape", "G") is None


def test_generic_entry_ids_are_url_safe_and_reversible_for_normal_keys() -> None:
    assert encoded_entry_id("Aaron") == "k-Aaron"
    entry_id = encoded_entry_id("ἀγάπη / love")
    assert entry_id.startswith("k-")
    assert "/" not in entry_id


def test_search_terms_fold_case_and_accents() -> None:
    assert search_key("KADESH") == "kadesh"
    assert search_key("ἀγάπη") == "αγαπη"
    assert search_key("Beth-el, the") == "beth-el the"


def test_link_candidates_separate_words_from_scripture() -> None:
    raw = '<a href="sword://Easton/MERIBAH">Meribah</a> and <ref target="Num.20.1">Num 20:1</ref>'
    assert link_candidates(raw) == {"MERIBAH"}
    assert link_candidates("see GREEK for 03056") == {"G3056"}
    assert link_candidates("see HEBREW for 0430") == {"H0430"}


def test_link_candidates_read_both_the_source_and_rendered_forms() -> None:
    # The same link is spelled differently in each form; both must be seen.
    assert link_candidates(
        '<ref target="ZIN">Zin</ref>', '<a href="sword://Easton/MERIBAH">Meribah</a>'
    ) == {"ZIN", "MERIBAH"}


def test_dictionary_emits_direct_strong_lookup(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    record, metadata = write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {
                "record_type": "entry",
                "key": "03056",
                "raw": "See John.1.1",
                "plain": "logos: a word",
                "html": "<p><em>logos</em>: a word</p>",
            }
        ],
        metadata={"feature": "GreekDef"},
    )
    document = json.loads((tmp_path / "strongsgreek/G3056.json").read_text(encoding="utf-8"))
    assert record["strong_prefix"] == "G"
    assert document["id"] == "G3056"
    assert document["occurrence"] == 1
    assert document["aliases"] == ["03056", "G3056"]
    assert document["text"] == "logos: a word"
    assert "html" not in document
    assert document["references"] == [{"osis": "John.1.1", "book": 43, "chapter": 1, "verse": 1}]
    assert metadata["index_url"] == "index.json"


def test_dictionary_index_is_sorted_and_slim(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {"key": "Zeta", "raw": "last", "plain": "last", "html": ""},
            {"key": "Alpha", "raw": "first", "plain": "first", "html": ""},
        ],
    )
    index = json.loads((tmp_path / "strongsgreek/index.json").read_text(encoding="utf-8"))
    assert index["schema"] == "getbible-dictionary-index-v1"
    assert [record["key"] for record in index["entries"]] == ["Alpha", "Zeta"]
    assert index["entries"][0] == {"id": "k-Alpha", "key": "Alpha", "search": "alpha"}
    assert index["entry_url_template"] == "{entry}.json"


def test_dictionary_links_resolve_in_both_directions(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {
                "key": "KADESH",
                "raw": 'also <a href="sword://Easton/MERIBAH">Meribah</a>',
                "plain": "A place in the wilderness.",
                "html": "",
            },
            {
                "key": "MERIBAH",
                "raw": "Waters of strife.",
                "plain": "Waters of strife.",
                "html": "",
            },
        ],
    )
    kadesh = json.loads((tmp_path / "strongsgreek/k-KADESH.json").read_text(encoding="utf-8"))
    meribah = json.loads((tmp_path / "strongsgreek/k-MERIBAH.json").read_text(encoding="utf-8"))
    assert kadesh["see_also"] == [{"id": "k-MERIBAH", "key": "MERIBAH"}]
    assert "backlinks" not in kadesh
    assert meribah["backlinks"] == [{"id": "k-KADESH", "key": "KADESH"}]
    assert "see_also" not in meribah


def test_dictionary_preserves_repeated_keys_as_distinct_definitions(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    record, _ = write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {"key": "03056", "raw": "First", "plain": "First", "html": ""},
            {"key": "03056", "raw": "Second", "plain": "Second", "html": ""},
        ],
        metadata={"feature": "GreekDef"},
    )
    first = json.loads((tmp_path / "strongsgreek/G3056.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "strongsgreek/G3056--2.json").read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "strongsgreek/index.json").read_text(encoding="utf-8"))

    assert first["text"] == "First"
    assert first["occurrence"] == 1
    assert second["text"] == "Second"
    assert second["occurrence"] == 2
    assert [item["id"] for item in index["entries"]] == ["G3056", "G3056--2"]
    assert index["entries"][1]["occurrence"] == 2
    assert record["entry_count"] == 2
    assert record["unique_key_count"] == 1


def test_whole_dictionary_embeds_entries_in_index_order(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    record, _ = write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {"key": "Zeta", "raw": "last", "plain": "last", "html": ""},
            {"key": "Alpha", "raw": "first", "plain": "first", "html": ""},
        ],
    )
    complete = json.loads((tmp_path / "strongsgreek.json").read_text(encoding="utf-8"))
    entries = [
        json.loads((tmp_path / f"strongsgreek/k-{name}.json").read_text(encoding="utf-8"))
        for name in ("Alpha", "Zeta")
    ]
    assert complete["schema"] == "getbible-dictionary-v1"
    assert complete["entries"] == entries
    assert record["bytes"] == (tmp_path / "strongsgreek.json").stat().st_size
