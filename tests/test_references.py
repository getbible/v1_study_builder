import json

import pytest

from study_builder.books import BookRegistry
from study_builder.references import ReferenceParser, split_ordinal


@pytest.fixture
def registry(project_root) -> BookRegistry:
    return BookRegistry(project_root / "conf/book_registry.json")


@pytest.fixture
def english(registry) -> ReferenceParser:
    return ReferenceParser(registry, "en")


def coordinates(items):
    return [
        (item["book"], item["chapter"], item.get("verse"), item.get("verses")) for item in items
    ]


def test_split_ordinal_reads_arabic_and_roman_prefixes() -> None:
    assert split_ordinal("Gen") == (None, "Gen")
    assert split_ordinal("1 Sam") == (1, "Sam")
    assert split_ordinal("II Kings") == (2, "Kings")
    assert split_ordinal("3 John") == (3, "John")


def test_thompson_chain_example_keeps_text_and_restores_the_book(english) -> None:
    items = english.extract("son of Adam, slain by Cain Ge 4:2,8; Mt 23:35; Heb 11:4; 12:24")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Ge 4:2,8", "Ge 4:2,8"),
        ("Mt 23:35", "Mt 23:35"),
        ("Heb 11:4", "Heb 11:4"),
        ("12:24", "Heb 12:24"),
    ]
    assert items[0] == {
        "text": "Ge 4:2,8",
        "ref": "Ge 4:2,8",
        "osis": "Gen.4.2",
        "book": 1,
        "chapter": 4,
        "verse": 2,
        "verses": [2, 8],
    }
    assert items[3] == {
        "text": "12:24",
        "ref": "Heb 12:24",
        "osis": "Heb.12.24",
        "book": 58,
        "chapter": 12,
        "verse": 24,
    }


def test_references_are_found_in_the_middle_of_prose_and_left_in_place(english) -> None:
    text = (
        "handmaid of Sarah Ge 16:1 Bore Abraham a son, Ishmael Ge 16:15 Cast out "
        "Ge 21:9-14; Ga 4:25"
    )
    items = english.extract(text)
    assert [item["text"] for item in items] == ["Ge 16:1", "Ge 16:15", "Ge 21:9-14", "Ga 4:25"]
    assert coordinates(items) == [
        (1, 16, 1, None),
        (1, 16, 15, None),
        (1, 21, 9, [9, 10, 11, 12, 13, 14]),
        (48, 4, 25, None),
    ]


def test_bookless_citation_in_prose_inherits_the_last_named_book(english) -> None:
    text = "(Judg. 8:33; 9:4). In 9:46 he is called son of Gideon, but Comp. 1:9; Ezek. 16:8."
    items = english.extract(text)
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Judg. 8:33", "Judg. 8:33"),
        ("9:4", "Judg. 9:4"),
        ("9:46", "Judg. 9:46"),
        ("1:9", "Judg. 1:9"),
        ("Ezek. 16:8", "Ezek. 16:8"),
    ]
    assert [item["book"] for item in items] == [7, 7, 7, 7, 26]


def test_citation_of_another_work_is_not_a_scripture_reference(english) -> None:
    text = (
        "Josephus relates (Ant. 11:8, 2-4) that Sanballat built it (Neh. 13:28); Enoch 6:6; 8:1 ff"
    )
    items = english.extract(text)
    assert [item["text"] for item in items] == ["Neh. 13:28"]


def test_ordinals_in_any_style_and_with_any_spacing(english) -> None:
    text = "(1 Chronicles 9:16) 2 Ki 5:12; 1Sa 21:10; II Co 3:4; I Jo 1:1"
    items = english.extract(text)
    assert [item["book"] for item in items] == [13, 12, 9, 47, 62]


def test_a_verse_number_before_a_book_is_not_an_ordinal(english) -> None:
    items = english.extract("Ps 1:2 Mt 3:4 Job 23:10 Ps 142.3")
    assert [(item["text"], item["book"]) for item in items] == [
        ("Ps 1:2", 19),
        ("Mt 3:4", 40),
        ("Job 23:10", 18),
        ("Ps 142.3", 19),
    ]
    assert items[3]["verse"] == 3


def test_whole_chapters_and_chapter_lists(english) -> None:
    items = english.extract(
        "Ex 28:1; 29:9; Nu 17; 18:1; Ps 99:6 → Consecration Ex 28; 29; Le 8 → Mt 5-7"
    )
    assert coordinates(items) == [
        (2, 28, 1, None),
        (2, 29, 9, None),
        (4, 17, None, None),
        (4, 18, 1, None),
        (19, 99, 6, None),
        (2, 28, None, None),
        (2, 29, None, None),
        (3, 8, None, None),
        (40, 5, None, None),
        (40, 6, None, None),
        (40, 7, None, None),
    ]
    assert items[2] == {"text": "Nu 17", "ref": "Nu 17", "osis": "Num.17", "book": 4, "chapter": 17}
    assert [item["text"] for item in items[8:]] == ["Mt 5-7"] * 3


def test_bare_number_after_semicolon_is_prose_when_a_word_follows(english) -> None:
    items = english.extract("Ps 99:6; 4 sons of Aaron; 5 daughters")
    assert [item["text"] for item in items] == ["Ps 99:6"]


def test_dot_notation_and_sentence_ending_periods(english) -> None:
    items = english.extract(
        "Gideon belonged, Joshua 17.2; Jud 6.34; 8.2. In this land Ex 32. He made"
    )
    assert coordinates(items) == [
        (6, 17, 2, None),
        (7, 6, 34, None),
        (7, 8, 2, None),
        (2, 32, None, None),
    ]
    assert items[3]["text"] == "Ex 32"


def test_cross_chapter_ranges_expand_with_the_registry_verse_counts(english) -> None:
    items = english.extract("his rod, Nu 16:1-17:13. He was faithful")
    assert coordinates(items) == [
        (4, 16, 1, list(range(1, 51))),
        (4, 17, 1, list(range(1, 14))),
    ]
    assert {item["text"] for item in items} == {"Nu 16:1-17:13"}
    items = english.extract("Ex 4:1-6:36")
    assert coordinates(items) == [
        (2, 4, 1, list(range(1, 32))),
        (2, 5, None, None),
        (2, 6, 1, list(range(1, 31))),
    ]


def test_verse_lists_with_spaces_and_chapter_changes(english) -> None:
    items = english.extract("Hos. 2:1, 23. See Gen. 18:2, 22; Heb 11:4, 12:24, 25; Isa 9:5 ff")
    assert coordinates(items) == [
        (28, 2, 1, [1, 23]),
        (1, 18, 2, [2, 22]),
        (58, 11, 4, None),
        (58, 12, 24, [24, 25]),
        (23, 9, 5, None),
    ]
    assert items[4]["text"] == "Isa 9:5 ff"


def test_verse_only_citations_inherit_book_and_chapter(english) -> None:
    items = english.extract(
        "Son of Bela (1 Chr. 8:3); called also Ahihud (ver. 7) and Iri (vv. 9-10, 12)."
    )
    assert [(item["text"], item["ref"]) for item in items] == [
        ("1 Chr. 8:3", "1 Chr. 8:3"),
        ("ver. 7", "1 Chr. 8:7"),
        ("vv. 9-10, 12", "1 Chr. 8:9-10, 12"),
    ]
    assert coordinates(items)[2] == (13, 8, 9, [9, 10, 12])
    assert english.extract("Pausanias v. 25 and Livy v.33.7") == []


def test_impossible_chapters_and_times_of_day_are_ignored(english) -> None:
    assert english.extract("1 Ki 30:1 and Ps 151:1") == []
    assert english.extract("Mt 5:3 at 10:30 a.m. and 11:15 pm") == [
        {
            "text": "Mt 5:3",
            "ref": "Mt 5:3",
            "osis": "Matt.5.3",
            "book": 40,
            "chapter": 5,
            "verse": 3,
        }
    ]


def test_lowercase_words_and_glued_names_are_not_books(english) -> None:
    assert english.extract("that is 6:1 of them, so 4:2 and am 3:4") == []
    assert english.extract("REVERENCEGe 4:2 Gehenna 3") == []
    assert english.extract("GENESIS 1:1")[0]["book"] == 1


def test_commentary_context_seeds_the_book_before_any_is_named(registry) -> None:
    parser = ReferenceParser(registry, "en")
    items = parser.extract("Compare 3:16 with Ro 5:8, and ver. 5 with 1:1.", book=43, chapter=1)
    assert [(item["text"], item["ref"], item["book"]) for item in items] == [
        ("3:16", "John 3:16", 43),
        ("Ro 5:8", "Ro 5:8", 45),
        ("ver. 5", "Ro 5:5", 45),
        ("1:1", "Ro 1:1", 45),
    ]
    assert parser.extract("3:16", book=43) == [
        {
            "text": "3:16",
            "ref": "John 3:16",
            "osis": "John.3.16",
            "book": 43,
            "chapter": 3,
            "verse": 16,
        }
    ]
    assert parser.extract("ver. 5", book=43, chapter=None) == []


def test_swedish_and_vietnamese_spellings(registry) -> None:
    swedish = ReferenceParser(registry, "sv")
    items = swedish.extract("Galban (2 Mos. 30:34. Syr. 24:15). Upp. 1:8; Apg. 2:1")
    assert [item["book"] for item in items] == [2, 74, 66, 44]
    vietnamese = ReferenceParser(registry, "vi")
    items = vietnamese.extract(
        "Lu 18:8; Công 12:7; 22:18; Rô 16:20quickness; 1 Cô 3:4; Gi 3:16; 1 Gi 1:1"
    )
    assert [item["book"] for item in items] == [42, 44, 44, 45, 46, 43, 62]
    assert items[3]["text"] == "Rô 16:20"


def test_languages_without_a_table_publish_nothing(registry) -> None:
    parser = ReferenceParser(registry, "tlh")
    assert parser.enabled is False
    assert parser.extract("Ge 4:2") == []


def test_registry_verse_counts_follow_the_kjv(registry) -> None:
    genesis = registry.by_number[1]
    assert genesis.chapter_count == 50
    assert genesis.verse_count(1) == 31
    assert genesis.verse_count(51) is None
    assert registry.by_number[19].verse_count(119) == 176
    assert sum(sum(book.verses) for book in registry.books if book.number <= 66) == 31102


def test_every_registry_name_is_recognised_as_its_own_book(registry) -> None:
    for language in ("en", "sv", "vi"):
        parser = ReferenceParser(registry, language)
        for book in registry.books:
            for name in book.names.get(language, ()):
                items = parser.extract(f"{name} 1:1")
                assert items and items[0]["book"] == book.number, (language, name, items)


def test_documents_stay_valid_json_in_text_order(english) -> None:
    text = "Priesthood of Ex 28:1; 29:9; Nu 17; 18:1; Ps 99:6"
    items = english.extract(text)
    round_trip = json.loads(json.dumps(items))
    assert round_trip == items
    assert [item["text"] for item in items] == ["Ex 28:1", "29:9", "Nu 17", "18:1", "Ps 99:6"]
