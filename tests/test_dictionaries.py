import hashlib
import json

import pytest

from study_builder.books import BookRegistry
from study_builder.dictionaries import (
    DictionaryWriter,
    canonical_strong,
    encoded_entry_id,
    link_candidates,
    search_key,
)
from study_builder.models import ModuleDescriptor, NativeExport


@pytest.fixture(autouse=True)
def _engine(reference_engine, shared_bible_api):
    global ENGINE, SHARED_API
    ENGINE = reference_engine
    SHARED_API = shared_bible_api


def write(tmp_path, project_root, module, entries, metadata=None, references=None):
    writer = DictionaryWriter(
        tmp_path,
        BookRegistry(project_root / "conf/book_registry.json"),
        project_root / "schemas",
        references=references or ENGINE,
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
                "raw": 'See <ref osisRef="John.1.1">John 1:1</ref>',
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
    assert document["references"] == [
        {"ref": "John 1:1", "osis": "John.1.1", "book": 43, "chapter": 1, "verse": 1}
    ]
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


@pytest.mark.parametrize("literal_first", [False, True])
def test_repeated_keys_do_not_claim_literal_suffix_paths(
    tmp_path, project_root, greek_dictionary_module, literal_first
) -> None:
    repeated = [{"key": "A", "plain": f"Definition {number}"} for number in range(1, 4)]
    literal = {"key": "A--2", "plain": "Literal suffix"}
    entries = [literal, *repeated] if literal_first else [*repeated, literal]
    record, _ = write(tmp_path, project_root, greek_dictionary_module, entries)

    expected = {
        "k-A": ("A", 1, "Definition 1"),
        "k-A--2": ("A--2", 1, "Literal suffix"),
        "k-A--3": ("A", 2, "Definition 2"),
        "k-A--4": ("A", 3, "Definition 3"),
    }
    index = json.loads((tmp_path / "strongsgreek/index.json").read_text(encoding="utf-8"))
    complete = json.loads((tmp_path / "strongsgreek.json").read_text(encoding="utf-8"))
    assert record["entry_count"] == len(expected)
    assert len({item["id"].casefold() for item in index["entries"]}) == len(expected)
    assert [item["id"] for item in complete["entries"]] == [item["id"] for item in index["entries"]]
    for document in complete["entries"]:
        assert (document["key"], document["occurrence"], document["text"]) == expected[
            document["id"]
        ]
        standalone = json.loads(
            (tmp_path / f"strongsgreek/{document['id']}.json").read_text(encoding="utf-8")
        )
        assert standalone == document


@pytest.mark.parametrize("keys", [("3056", "03056"), ("a/b", "YS9i"), ("ALPHA", "alpha")])
@pytest.mark.parametrize("reverse", [False, True])
def test_distinct_key_collisions_keep_each_repeated_definition(
    tmp_path, project_root, greek_dictionary_module, keys, reverse
) -> None:
    first, second = reversed(keys) if reverse else keys
    record, _ = write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {"key": first, "plain": "First key, first definition"},
            {"key": second, "plain": "Second key, first definition"},
            {"key": first, "plain": "First key, second definition"},
            {"key": second, "plain": "Second key, second definition"},
        ],
    )
    first_id = canonical_strong(first, "G") or encoded_entry_id(first)
    second_id = "h-" + hashlib.sha256(second.encode("utf-8")).hexdigest()
    expected = {
        first_id: (first, 1, "First key, first definition"),
        second_id: (second, 1, "Second key, first definition"),
        first_id + "--2": (first, 2, "First key, second definition"),
        second_id + "--2": (second, 2, "Second key, second definition"),
    }
    complete = json.loads((tmp_path / "strongsgreek.json").read_text(encoding="utf-8"))
    assert record["entry_count"] == len(expected)
    assert {
        item["id"]: (item["key"], item["occurrence"], item["text"]) for item in complete["entries"]
    } == expected
    assert len({item["id"].casefold() for item in complete["entries"]}) == len(expected)


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


def test_references_are_read_from_prose_and_markup_alike(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    text = "son of Adam, slain by Cain Ge 4:2,8; Mt 23:35; Heb 11:4; 12:24"
    write(
        tmp_path,
        project_root,
        greek_dictionary_module,
        [
            {"key": "ABEL", "raw": text, "plain": text, "html": ""},
            {
                "key": "CAIN",
                "raw": '<reference osisRef="Gen.4.1">Gen. 4:1</reference> slew Ge 4:8; see 9:20',
                "plain": "Gen. 4:1 slew Ge 4:8; see 9:20",
                "html": "",
            },
            {"key": "SETH", "raw": "third son", "plain": "third son", "html": ""},
        ],
    )
    abel = json.loads((tmp_path / "strongsgreek/k-ABEL.json").read_text(encoding="utf-8"))
    cain = json.loads((tmp_path / "strongsgreek/k-CAIN.json").read_text(encoding="utf-8"))
    seth = json.loads((tmp_path / "strongsgreek/k-SETH.json").read_text(encoding="utf-8"))
    assert abel["text"] == text
    assert [(item["text"], item["ref"]) for item in abel["references"]] == [
        ("Ge 4:2,8", "Genesis 4:2,8"),
        ("Mt 23:35", "Matthew 23:35"),
        ("Heb 11:4", "Hebrews 11:4"),
        ("12:24", "Hebrews 12:24"),
    ]
    assert abel["references"][3] == {
        "text": "12:24",
        "ref": "Hebrews 12:24",
        "osis": "Heb.12.24",
        "book": 58,
        "chapter": 12,
        "verse": 24,
    }
    # The markup is authoritative for what it covers; the prose around it is read too.
    assert [(item["text"], item["ref"]) for item in cain["references"]] == [
        ("Gen. 4:1", "Genesis 4:1"),
        ("Ge 4:8", "Genesis 4:8"),
        ("9:20", "Genesis 9:20"),
    ]
    assert "references" not in seth


def test_a_language_without_tables_reads_the_api_names_and_the_shape_spellings(
    tmp_path, project_root
) -> None:
    module = ModuleDescriptor(
        name="Klingon",
        conf_path="mods.d/klingon.conf",
        fields={"description": ("Klingon",), "lang": ("tlh",), "distributionlicense": ("PD",)},
    )
    from study_builder.books import BookRegistry as Registry
    from study_builder.references import ReferenceEngine

    engine = ReferenceEngine.for_module(
        SHARED_API,
        Registry(project_root / "conf/book_registry.json"),
        "tlh",
        "",
        project_root / "conf/book_aliases",
    )
    write(
        tmp_path,
        project_root,
        module,
        [{"key": "A", "raw": "Ge 4:2", "plain": "Ge 4:2", "html": ""}],
        references=engine,
    )
    document = json.loads((tmp_path / "klingon/k-A.json").read_text(encoding="utf-8"))
    assert document["references"] == [
        {
            "text": "Ge 4:2",
            "ref": "Genesis 4:2",
            "osis": "Gen.4.2",
            "book": 1,
            "chapter": 4,
            "verse": 2,
        }
    ]
    metadata = json.loads((tmp_path / "klingon/metadata.json").read_text(encoding="utf-8"))
    assert metadata["references"]["names"] == "klv"
    assert metadata["references"]["translations"] == ["kjv", "kjva"]


def test_thml_text_keeps_its_breaks_and_markup_passages(tmp_path, project_root) -> None:
    module = ModuleDescriptor(
        name="Torrey",
        conf_path="mods.d/torrey.conf",
        fields={
            "description": ("Torrey",),
            "lang": ("en",),
            "sourcetype": ("ThML",),
            "distributionlicense": ("Public Domain",),
        },
    )
    raw = (
        '- God condemns <scripRef passage="Ge 11:7">Ge 11:7</scripRef>; Isa 5:8<br>'
        "- Christ condemns Mt 18:1,3,4; 20:25<br>\n- Saints avoid Ps 131:1"
    )
    write(
        tmp_path,
        project_root,
        module,
        [{"key": "AMBITION", "raw": raw, "plain": "flattened by SWORD", "html": ""}],
    )
    document = json.loads((tmp_path / "torrey/k-AMBITION.json").read_text(encoding="utf-8"))
    assert document["text"] == (
        "- God condemns Ge 11:7; Isa 5:8\n"
        "- Christ condemns Mt 18:1,3,4; 20:25\n"
        "- Saints avoid Ps 131:1"
    )
    assert [(item["text"], item["ref"]) for item in document["references"]] == [
        ("Ge 11:7", "Genesis 11:7"),
        ("Isa 5:8", "Isaiah 5:8"),
        ("Mt 18:1,3,4", "Matthew 18:1,3-4"),
        ("20:25", "Matthew 20:25"),
        ("Ps 131:1", "Psalms 131:1"),
    ]
