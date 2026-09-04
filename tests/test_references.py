import json
from pathlib import Path

import pytest

from study_builder.books import BookRegistry
from study_builder.references import MarkupReference, ReferenceEngine, split_ordinal


@pytest.fixture(scope="module")
def registry() -> BookRegistry:
    return BookRegistry(Path(__file__).resolve().parents[1] / "conf/book_registry.json")


@pytest.fixture(scope="module")
def aliases_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "conf/book_aliases"


@pytest.fixture(scope="module")
def engines(bible_tree, tmp_path_factory, registry, aliases_dir):
    from study_builder.bible import BibleApi

    api = BibleApi(bible_tree, cache_dir=tmp_path_factory.mktemp("bible-cache"))

    def build(language: str, versification: str) -> ReferenceEngine:
        return ReferenceEngine.for_module(api, registry, language, versification, aliases_dir)

    return build


@pytest.fixture(scope="module")
def english(engines) -> ReferenceEngine:
    return engines("en", "KJV")


def coordinates(items):
    return [
        (item["book"], item["chapter"], item.get("verse"), item.get("verses")) for item in items
    ]


def test_split_ordinal_reads_arabic_roman_and_word_prefixes() -> None:
    assert split_ordinal("Gen") == (None, "Gen")
    assert split_ordinal("1 Sam") == (1, "Sam")
    assert split_ordinal("II Kings") == (2, "Kings")
    assert split_ordinal("3 John") == (3, "John")
    assert split_ordinal("First Samuel") == (1, "Samuel")
    assert split_ordinal("2nd Peter") == (2, "Peter")
    assert split_ordinal("5 Mos") == (5, "Mos")


def test_thompson_chain_example_keeps_text_and_publishes_a_canonical_ref(english) -> None:
    items = english.extract("son of Adam, slain by Cain Ge 4:2,8; Mt 23:35; Heb 11:4; 12:24")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Ge 4:2,8", "Genesis 4:2,8"),
        ("Mt 23:35", "Matthew 23:35"),
        ("Heb 11:4", "Hebrews 11:4"),
        ("12:24", "Hebrews 12:24"),
    ]
    assert items[0] == {
        "text": "Ge 4:2,8",
        "ref": "Genesis 4:2,8",
        "osis": "Gen.4.2",
        "book": 1,
        "chapter": 4,
        "verse": 2,
        "verses": [2, 8],
    }
    assert items[3] == {
        "text": "12:24",
        "ref": "Hebrews 12:24",
        "osis": "Heb.12.24",
        "book": 58,
        "chapter": 12,
        "verse": 24,
    }


def test_the_ref_is_what_the_librarian_and_the_query_api_accept(english) -> None:
    from getbible import GetBibleReference

    librarian = GetBibleReference()
    items = english.extract("Ge 21:9-14; Re 1:8; Song 2:1; Ps 119:105, 176; Nu 17")
    assert [item["ref"] for item in items] == [
        "Genesis 21:9-14",
        "Revelation 1:8",
        "Song of Solomon 2:1",
        "Psalms 119:105,176",
        "Numbers 17",
    ]
    for item in items:
        parsed = librarian.ref(item["ref"], "kjv")
        assert parsed.book == item["book"] and parsed.chapter == item["chapter"]
        if "verse" in item:
            assert parsed.verses == item.get("verses", [item["verse"]])


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
        ("Judg. 8:33", "Judges 8:33"),
        ("9:4", "Judges 9:4"),
        ("9:46", "Judges 9:46"),
        ("1:9", "Judges 1:9"),
        ("Ezek. 16:8", "Ezekiel 16:8"),
    ]
    assert [item["book"] for item in items] == [7, 7, 7, 7, 26]


def test_citation_of_another_work_is_not_a_scripture_reference(english) -> None:
    text = (
        "Josephus relates (Ant. 11:8, 2-4) that Sanballat built it (Neh. 13:28); Enoch 6:6; 8:1 ff"
    )
    items = english.extract(text)
    assert [item["text"] for item in items] == ["Neh. 13:28"]


def test_a_capitalised_word_mid_sentence_names_another_work(english) -> None:
    # "Or" opens no sentence here, so it belongs to the title "Sib Or" and 3:271 is not
    # Matthew; at the start of a sentence the same word introduces scripture.
    items = english.extract("Mt 3:1. Sib Or 3:271 is later. Or 4:2 is the same book.")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Mt 3:1", "Matthew 3:1"),
        ("4:2", "Matthew 4:2"),
    ]
    items = english.extract('Mt 1:1 in "Illustrations of Scripture," 1:303, see 2:2')
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Mt 1:1", "Matthew 1:1"),
        ("2:2", "Matthew 2:2"),
    ]


def test_times_ratios_and_pages_are_not_citations(english) -> None:
    items = english.extract("Mt 5:3 at 10:30 a.m., 9:15 A.M. and 11:15 pm; the ratio 3:6; vol. 2:4")
    assert [item["text"] for item in items] == ["Mt 5:3"]
    # A meridiem followed by a chapter is Amos, and a space alone continues a list.
    items = english.extract("Pleiades, Job 9:9 38:31 Am 5:8; the Great Bear Ca 4:7")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Job 9:9", "Job 9:9"),
        ("38:31", "Job 38:31"),
        ("Am 5:8", "Amos 5:8"),
        ("Ca 4:7", "Song of Solomon 4:7"),
    ]


def test_a_dash_before_a_book_joins_two_citations(english) -> None:
    items = english.extract("David 2Sa 2:4-1Ki 2:11; 1Ch 11:1-2; Ps 1:1, 2-1Sa 3:4")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("2Sa 2:4", "2 Samuel 2:4"),
        ("1Ki 2:11", "1 Kings 2:11"),
        ("1Ch 11:1-2", "1 Chronicles 11:1-2"),
        ("Ps 1:1, 2", "Psalms 1:1-2"),
        ("1Sa 3:4", "1 Samuel 3:4"),
    ]


def test_headwords_and_topic_cross_references_are_not_chapters(english) -> None:
    assert english.extract("PHILIP (1). See HEBREWS 2. and PHILIPPIANS 4.") == []
    # A bracketed citation with a verse, and an all-capital name with one, are real.
    items = english.extract("Mark (5:1) and GENESIS 1:1")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("5:1", "Mark 5:1"),
        ("GENESIS 1:1", "Genesis 1:1"),
    ]


def test_a_verse_list_stops_where_the_next_book_starts(english) -> None:
    items = english.extract("Ps 1:1, 2 Sam 3:4; Ex 3, 1 Ki 2:3")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Ps 1:1", "Psalms 1:1"),
        ("2 Sam 3:4", "2 Samuel 3:4"),
        ("Ex 3", "Exodus 3"),
        ("1 Ki 2:3", "1 Kings 2:3"),
    ]


def test_solomon_is_a_king_and_wisdom_is_not_the_song(english) -> None:
    assert english.extract("Written by Solomon (1:12).") == []
    items = english.extract("In The Wisdom of Solomon 13:9 and Song of Solomon 2:1")
    assert [(item["book"], item["text"]) for item in items] == [
        (73, "Wisdom of Solomon 13:9"),
        (22, "Song of Solomon 2:1"),
    ]


def test_ordinals_in_any_style_and_with_any_spacing(english) -> None:
    text = (
        "(1 Chronicles 9:16) 2 Ki 5:12; 1Sa 21:10; II Co 3:4; I Jo 1:1; First Peter 1:3; III Ki 2:1"
    )
    items = english.extract(text)
    assert [item["book"] for item in items] == [13, 12, 9, 47, 62, 60, 11]


def test_a_verse_number_before_a_book_is_not_an_ordinal(english) -> None:
    items = english.extract("Ps 1:2 Mt 3:4 Job 23:10 Ps 142.3")
    assert [(item["text"], item["book"]) for item in items] == [
        ("Ps 1:2", 19),
        ("Mt 3:4", 40),
        ("Job 23:10", 18),
        ("Ps 142.3", 19),
    ]
    assert items[3]["verse"] == 3
    assert items[3]["ref"] == "Psalms 142:3"


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
    assert items[2] == {
        "text": "Nu 17",
        "ref": "Numbers 17",
        "osis": "Num.17",
        "book": 4,
        "chapter": 17,
    }
    assert [item["text"] for item in items[8:]] == ["Mt 5-7"] * 3
    assert [item["ref"] for item in items[8:]] == ["Matthew 5", "Matthew 6", "Matthew 7"]


def test_a_comma_may_separate_the_name_from_a_chapter_and_verse(english) -> None:
    items = english.extract("Again, in Philippians, 3:13, 14, we are told")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("Philippians, 3:13, 14", "Philippians 3:13-14")
    ]
    # A general dictionary's "See Ho, 2." points at sense 2 of the word Ho.
    assert english.extract("Whoa, interj. Stop; stand; hold. See Ho, 2. From Mat, 4.") == []


def test_a_month_or_a_chapter_running_into_prose_is_not_a_citation(english) -> None:
    assert english.extract("the 2d, 5th and Mar. 8, and again Mar. 12 are leap days") == []
    assert english.extract("as in Ex 3, which he wrote") == []
    items = english.extract("Consecration Ex 28; 29; Le 8. In this land Ex 32. He made")
    assert [item["ref"] for item in items] == ["Exodus 28", "Exodus 29", "Leviticus 8", "Exodus 32"]


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


def test_cross_chapter_ranges_expand_with_the_api_verse_counts(english) -> None:
    items = english.extract("his rod, Nu 16:1-17:13. He was faithful")
    assert coordinates(items) == [
        (4, 16, 1, list(range(1, 51))),
        (4, 17, 1, list(range(1, 14))),
    ]
    assert {item["text"] for item in items} == {"Nu 16:1-17:13"}
    assert [item["ref"] for item in items] == ["Numbers 16:1-50", "Numbers 17:1-13"]
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
    assert items[0]["ref"] == "Hosea 2:1,23"


def test_verse_only_citations_inherit_book_and_chapter(english) -> None:
    items = english.extract(
        "Son of Bela (1 Chr. 8:3); called also Ahihud (ver. 7) and Iri (vv. 9-10, 12)."
    )
    assert [(item["text"], item["ref"]) for item in items] == [
        ("1 Chr. 8:3", "1 Chronicles 8:3"),
        ("ver. 7", "1 Chronicles 8:7"),
        ("vv. 9-10, 12", "1 Chronicles 8:9-10,12"),
    ]
    assert coordinates(items)[2] == (13, 8, 9, [9, 10, 12])
    assert english.extract("Pausanias v. 25 and Livy v.33.7") == []


def test_chapters_and_verses_the_api_does_not_have_are_not_published(english) -> None:
    assert english.extract("1 Ki 30:1 and Ps 151:1") == []
    assert english.extract("Ge 1:99 and Ps 23:200 and Genesis 50:36") == []
    items = english.extract("Ge 1:30-40 and Ps 23:5-9")
    assert coordinates(items) == [(1, 1, 30, [30, 31]), (19, 23, 5, [5, 6])]


def test_lowercase_words_and_glued_names_are_not_books(english) -> None:
    assert english.extract("that is 6:1 of them, so 4:2 and am 3:4") == []
    assert english.extract("REVERENCEGe 4:2 Gehenna 3") == []
    assert english.extract("GENESIS 1:1")[0]["book"] == 1


def test_commentary_context_seeds_the_book_before_any_is_named(english) -> None:
    items = english.extract("Compare 3:16 with Ro 5:8, and ver. 5 with 1:1.", book=43, chapter=1)
    assert [(item["text"], item["ref"], item["book"]) for item in items] == [
        ("3:16", "John 3:16", 43),
        ("Ro 5:8", "Romans 5:8", 45),
        ("ver. 5", "Romans 5:5", 45),
        ("1:1", "Romans 1:1", 45),
    ]
    assert english.extract("3:16", book=43) == [
        {
            "text": "3:16",
            "ref": "John 3:16",
            "osis": "John.3.16",
            "book": 43,
            "chapter": 3,
            "verse": 16,
        }
    ]
    assert english.extract("ver. 5", book=43, chapter=None) == []
    assert english.extract("ch. 1:13 and Nu 21:6-9", book=43, chapter=3)[0]["ref"] == "John 1:13"


def test_online_bible_spellings_the_librarian_lacks_come_from_the_supplement(english) -> None:
    items = english.extract("Lu 18:8; Joe 2:28; Jud 6:34; 1Jo 1:1; 3Jo 1:9; Isai 6:1; Reve 1:8")
    assert [(item["text"], item["book"]) for item in items] == [
        ("Lu 18:8", 42),
        ("Joe 2:28", 29),
        ("Jud 6:34", 7),
        ("1Jo 1:1", 62),
        ("3Jo 1:9", 64),
        ("Isai 6:1", 23),
        ("Reve 1:8", 66),
    ]
    assert items[0]["ref"] == "Luke 18:8"


def test_markup_references_are_authoritative_for_the_text_they_cover(english) -> None:
    text = "The serpent Nu 21:6-9 shows it; compare (Exodus 4:14) and (9:20) and Ge 1:1."
    items = english.extract(
        text,
        markup=[
            MarkupReference("osis", "Num.21.6-Num.21.9", "Nu 21:6-9"),
            MarkupReference("passage", "Exodus 4:14", "Exodus 4:14"),
            MarkupReference("passage", "Deuteronomy 9:20", "9:20"),
        ],
    )
    assert [(item.get("text"), item["ref"]) for item in items] == [
        ("Nu 21:6-9", "Numbers 21:6-9"),
        ("Exodus 4:14", "Exodus 4:14"),
        ("9:20", "Deuteronomy 9:20"),
        ("Ge 1:1", "Genesis 1:1"),
    ]
    assert items[0]["verses"] == [6, 7, 8, 9]


def test_markup_the_text_does_not_spell_out_is_published_without_text(english) -> None:
    items = english.extract(
        "See the note above.",
        markup=[
            MarkupReference("osis", "Gen.1.1 Gen.1.3", ""),
            MarkupReference("osis", "Gen.2", ""),
        ],
    )
    assert items == [
        {"ref": "Genesis 1:1", "osis": "Gen.1.1", "book": 1, "chapter": 1, "verse": 1},
        {"ref": "Genesis 1:3", "osis": "Gen.1.3", "book": 1, "chapter": 1, "verse": 3},
        {"ref": "Genesis 2", "osis": "Gen.2", "book": 1, "chapter": 2},
    ]


def test_a_prose_list_stops_where_the_markup_takes_over(english) -> None:
    # The source marks only the second citation up, and says it is Deuteronomy; the
    # prose list before it must not read "9:20" as Exodus as well.
    items = english.extract(
        "See Exodus 4:14; 9:20 and Ge 1:1, 2 there.",
        markup=[
            MarkupReference("passage", "Deuteronomy 9:20", "9:20"),
            MarkupReference("osis", "Gen.1.2", "2"),
        ],
    )
    assert [(item.get("text"), item["ref"]) for item in items] == [
        ("Exodus 4:14", "Exodus 4:14"),
        ("9:20", "Deuteronomy 9:20"),
        ("Ge 1:1", "Genesis 1:1"),
        ("2", "Genesis 1:2"),
    ]


def test_unresolvable_markup_still_shields_its_text_from_prose_reading(english) -> None:
    # The passage names a book the API has no translation for, so nothing is published
    # for it, but "9:20" must not then be read as the previous book's chapter.
    items = english.extract(
        "(Exodus 4:14) and (9:20).",
        markup=[
            MarkupReference("passage", "Exodus 4:14", "Exodus 4:14"),
            MarkupReference("passage", "Enoch 9:20", "9:20"),
        ],
    )
    assert [item["ref"] for item in items] == ["Exodus 4:14"]


def test_osis_forms_the_markup_may_carry(english) -> None:
    assert english.from_osis("Bible:Gen.1.1!a")[0]["ref"] == "Genesis 1:1"
    assert [item["ref"] for item in english.from_osis("Ps.23.1-Ps.23.6")] == ["Psalms 23:1-6"]
    assert [item["ref"] for item in english.from_osis("Ps.23.1-6")] == ["Psalms 23:1-6"]
    assert [item["ref"] for item in english.from_osis("Exod.4.1-Exod.6.36")] == [
        "Exodus 4:1-31",
        "Exodus 5",
        "Exodus 6:1-30",
    ]
    assert [item["ref"] for item in english.from_osis("Matt.5-Matt.7")] == [
        "Matthew 5",
        "Matthew 6",
        "Matthew 7",
    ]
    assert english.from_osis("Gen") == []
    assert english.from_osis("Nowhere.1.1") == []


def test_a_long_topical_verse_list_is_published_whole(english) -> None:
    from getbible import GetBibleReference

    numbers = [2, 4, 5, 6, 8, 10, 15, 16, 22, 30, 31, 33, 44, 45, 55, 56, 59, 60, 72, 77, 87]
    numbers += [97, 100, 101, 102, 104, 105, 106, 109, 112, 129, 167, 168]
    text = "Ps 119:2,4-6,8,10,15-16,22,30-31,33,44-45,55-56,59-60,72,77,87,97,100-102"
    text += ",104-106,109,112,129,167-168"
    items = english.extract(text)
    assert len(items) == 1
    assert items[0]["verses"] == numbers
    assert items[0]["ref"] == "Psalms " + text.split(" ", 1)[1]
    # Longer than a public Query API request may be, but the same grammar.
    parsed = GetBibleReference(max_reference_length=4096).ref(items[0]["ref"], "kjv")
    assert parsed.verses == numbers


def test_duplicates_are_published_once_in_text_order(english) -> None:
    items = english.extract("Ge 1:1 and again Ge 1:1; Ge 1:1-2")
    assert [item["ref"] for item in items] == ["Genesis 1:1", "Genesis 1:1-2"]


def test_swedish_and_vietnamese_modules_publish_in_their_own_names(engines) -> None:
    swedish = engines("sv", "NRSVA")
    items = swedish.extract(
        "Galban (2 Mos. 30:34. Syr. 24:15). Upp. 1:8; Apg. 2:1; 1 Kon. 2:3; Höga V. 4:14"
    )
    assert [(item["book"], item["ref"]) for item in items] == [
        (2, "Andra Moseboken 30:34"),
        (74, "Jesus Syraks visdom 24:15"),
        (66, "Uppenbarelseboken 1:8"),
        (44, "Apostlagärningarna 2:1"),
    ]
    # Song of Songs is not in the Swedish fixture, but its name is recognised and consumed.
    assert swedish.aliases.find("Höga V. 4:14", 0)[2] == 22
    assert swedish.describe()["translations"] == ["swedish"]

    vietnamese = engines("vi", "")
    items = vietnamese.extract(
        "Lu 18:8; Công 12:7; 22:18; Rô 16:20quickness; 1 Cô 3:4; Gi 3:16; 1 Gi 1:1; đếnMa 24:12"
    )
    assert [item["book"] for item in items] == [42, 44, 44, 45, 46, 43, 62, 40]
    assert items[3]["text"] == "Rô 16:20"
    assert items[0]["ref"] == "Lu-ca 18:8"
    assert items[7]["ref"] == "Ma-thi-ơ 24:12"
    # A Strong's number runs straight into an ordinal, and a name into its chapter.
    items = vietnamese.extract("Xem G16402 Cô 8:15; G1510Ma12:2,4; H894Ma 1:11")
    assert [(item["text"], item["ref"]) for item in items] == [
        ("2 Cô 8:15", "2 Cô-rinh-tô 8:15"),
        ("Ma12:2,4", "Ma-thi-ơ 12:2,4"),
        ("Ma 1:11", "Ma-thi-ơ 1:11"),
    ]
    assert vietnamese.describe()["translations"] == ["kjv", "kjva"]
    assert vietnamese.describe()["names"] == "vietnamese"


def test_a_module_is_checked_against_its_own_versification(engines) -> None:
    latin = engines("la", "Vulg")
    assert latin.extract("Ps 10:39")[0]["ref"] == "Psalmi 10:39"
    assert engines("en", "KJV").extract("Ps 10:39") == []


def test_a_language_without_tables_still_reads_the_api_names(engines) -> None:
    klingon = engines("tlh", "")
    assert klingon.describe()["librarian"] == ["kjv"]
    assert klingon.extract("Genesis 1:1")[0]["ref"] == "Genesis 1:1"


def test_documents_stay_valid_json_in_text_order(english) -> None:
    text = "Priesthood of Ex 28:1; 29:9; Nu 17; 18:1; Ps 99:6"
    items = english.extract(text)
    round_trip = json.loads(json.dumps(items))
    assert round_trip == items
    assert [item["text"] for item in items] == ["Ex 28:1", "29:9", "Nu 17", "18:1", "Ps 99:6"]
