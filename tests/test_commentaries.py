import json

import pytest

from study_builder.books import BookRegistry
from study_builder.commentaries import CommentaryWriter
from study_builder.models import NativeExport
from study_builder.util import DocumentTooLarge


class OnePassEntries:
    def __init__(self, entries):
        self.entries = entries
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        assert self.passes <= 1, "commentary entries were loaded or traversed more than once"
        yield from self.entries

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, index):
        return self.entries[index]


def entry(osis, book, chapter, verse, text, *, raw=None, html=""):
    return {
        "record_type": "entry",
        "key": osis,
        "raw": raw if raw is not None else text,
        "plain": text,
        "html": html,
        "verse": {
            "osis": osis,
            "testament": 1 if book <= 39 else 2,
            "book": book if book <= 39 else book - 39,
            "chapter": chapter,
            "verse": verse,
        },
    }


@pytest.fixture(autouse=True)
def _engine(reference_engine):
    global ENGINE
    ENGINE = reference_engine


def write(tmp_path, project_root, module, entries, max_document_bytes=0):
    writer = CommentaryWriter(
        tmp_path,
        BookRegistry(project_root / "conf/book_registry.json"),
        project_root / "schemas",
        max_document_bytes,
        references=ENGINE,
    )
    return writer.write(module, NativeExport(metadata={"record_type": "module"}, entries=entries))


def test_commentary_matches_v3_book_chapter_verse_contract(
    tmp_path, project_root, commentary_module
) -> None:
    record, metadata = write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry(
                "John.1.1",
                43,
                1,
                1,
                "A comment on creation.",
                raw='<reference osisRef="John.1.1">John 1:1</reference>',
                html="<p>A comment on <strong>creation</strong>.</p>",
            )
        ],
    )
    chapter = json.loads((tmp_path / "testcom/43/1.json").read_text(encoding="utf-8"))
    assert metadata["chapter_url_template"] == "{book}/{chapter}.json"
    assert (chapter["book"], chapter["chapter"]) == (43, 1)
    assert chapter["entries"][0]["verse"] == 1
    assert chapter["entries"][0]["osis"] == "John.1.1"
    assert chapter["entries"][0]["references"][0]["book"] == 43
    assert record["entry_count"] == 1


def test_commentary_publishes_text_without_markup(
    tmp_path, project_root, commentary_module
) -> None:
    write(
        tmp_path,
        project_root,
        commentary_module,
        [entry("Gen.1.1", 1, 1, 1, "Plain words.", html="<p>Plain <em>words</em>.</p>")],
    )
    chapter = json.loads((tmp_path / "testcom/1/1.json").read_text(encoding="utf-8"))
    published = chapter["entries"][0]
    assert published["text"] == "Plain words."
    assert "html" not in published
    assert "html" not in (tmp_path / "testcom/1/1.json").read_text(encoding="utf-8")


def test_commentary_publishes_book_and_chapter_introductions(
    tmp_path, project_root, commentary_module
) -> None:
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("Dan.0.0", 27, 0, 0, "About the book of Daniel."),
            entry("Dan.1.0", 27, 1, 0, "About chapter one."),
            entry("Dan.1.1", 27, 1, 1, "On the first verse."),
        ],
    )
    introduction = json.loads((tmp_path / "testcom/27/0.json").read_text(encoding="utf-8"))
    assert introduction["chapter"] == 0
    assert introduction["name"] == "Daniel"
    assert introduction["entries"][0]["text"] == "About the book of Daniel."

    chapter = json.loads((tmp_path / "testcom/27/1.json").read_text(encoding="utf-8"))
    assert [item["verse"] for item in chapter["entries"]] == [0, 1]
    assert chapter["name"] == "Daniel"

    books = json.loads((tmp_path / "testcom/books.json").read_text(encoding="utf-8"))
    assert books["books"][0]["chapters"] == [0, 1]


def test_book_and_commentary_documents_embed_their_parts_verbatim(
    tmp_path, project_root, commentary_module
) -> None:
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("Gen.1.1", 1, 1, 1, "First."),
            entry("Gen.2.1", 1, 2, 1, "Second."),
            entry("John.1.1", 43, 1, 1, "Third."),
        ],
    )
    chapters = [
        json.loads((tmp_path / f"testcom/1/{number}.json").read_text(encoding="utf-8"))
        for number in (1, 2)
    ]
    book = json.loads((tmp_path / "testcom/1.json").read_text(encoding="utf-8"))
    assert book["schema"] == "getbible-commentary-book-v1"
    assert book["chapters"] == chapters

    complete = json.loads((tmp_path / "testcom.json").read_text(encoding="utf-8"))
    books = [
        json.loads((tmp_path / f"testcom/{number}.json").read_text(encoding="utf-8"))
        for number in (1, 43)
    ]
    assert complete["schema"] == "getbible-commentary-v1"
    assert complete["books"] == books


def test_commentary_reports_counts_and_bulk_size(tmp_path, project_root, commentary_module) -> None:
    record, metadata = write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("Gen.1.1", 1, 1, 1, "First."),
            entry("Gen.2.1", 1, 2, 1, "Second."),
            entry("John.1.1", 43, 1, 1, "Third."),
        ],
    )
    assert (record["book_count"], record["chapter_count"], record["entry_count"]) == (2, 3, 3)
    assert record["bytes"] == (tmp_path / "testcom.json").stat().st_size
    assert metadata["bytes"] == record["bytes"]


def test_commentary_consumes_source_entries_once(tmp_path, project_root, commentary_module) -> None:
    entries = OnePassEntries(
        [
            entry("Gen.1.1", 1, 1, 1, "First chapter"),
            entry("Gen.2.1", 1, 2, 1, "Second chapter"),
        ]
    )
    write(tmp_path, project_root, commentary_module, entries)
    assert entries.passes == 1
    assert (tmp_path / "testcom/1/1.json").is_file()
    assert (tmp_path / "testcom/1/2.json").is_file()


def test_range_comment_is_stored_once_and_lists_every_verse_it_covers(
    tmp_path, project_root, commentary_module
) -> None:
    """A SWORD range comment arrives once per verse; it must be published once."""
    exposition = "One exposition covering the whole passage."
    record, metadata = write(
        tmp_path,
        project_root,
        commentary_module,
        [entry(f"Ps.119.{number}", 19, 119, number, exposition) for number in range(1, 9)],
    )
    chapter = json.loads((tmp_path / "testcom/19/119.json").read_text(encoding="utf-8"))

    assert len(chapter["entries"]) == 1
    published = chapter["entries"][0]
    assert published["text"] == exposition
    assert published["verse"] == 1
    assert published["verses"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert published["osis"] == "Ps.119.1"
    assert record["entry_count"] == 1

    # The document holds the text once, not once per verse it covers.
    assert (tmp_path / "testcom/19/119.json").read_text(encoding="utf-8").count(exposition) == 1
    assert metadata["storage"]["source_entry_count"] == 8
    assert metadata["storage"]["repetition_ratio"] == 8.0


def test_distinct_comments_in_one_chapter_are_all_kept(
    tmp_path, project_root, commentary_module
) -> None:
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("Gen.1.1", 1, 1, 1, "On verse one."),
            entry("Gen.1.2", 1, 1, 2, "On verse two."),
            entry("Gen.1.3", 1, 1, 3, "On verse three."),
        ],
    )
    chapter = json.loads((tmp_path / "testcom/1/1.json").read_text(encoding="utf-8"))
    assert [item["verse"] for item in chapter["entries"]] == [1, 2, 3]
    assert all("verses" not in item for item in chapter["entries"])


def test_a_comment_spanning_two_chapters_stays_in_both(
    tmp_path, project_root, commentary_module
) -> None:
    """Chapter documents must stand alone, so the collapse stops at the boundary."""
    shared = "A comment that spans the chapter break."
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("Gen.1.31", 1, 1, 31, shared),
            entry("Gen.2.1", 1, 2, 1, shared),
        ],
    )
    first = json.loads((tmp_path / "testcom/1/1.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "testcom/1/2.json").read_text(encoding="utf-8"))
    assert first["entries"][0]["text"] == shared
    assert second["entries"][0]["text"] == shared


def test_collapsed_entry_keeps_every_reference_from_the_range(
    tmp_path, project_root, commentary_module
) -> None:
    shared = "A comment on the passage."
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry(
                "Gen.1.1", 1, 1, 1, shared, raw='see <reference osisRef="John.3.16">it</reference>'
            ),
            entry(
                "Gen.1.2", 1, 1, 2, shared, raw='see <reference osisRef="Rom.5.8">it</reference>'
            ),
        ],
    )
    chapter = json.loads((tmp_path / "testcom/1/1.json").read_text(encoding="utf-8"))
    published = chapter["entries"][0]
    assert published["verses"] == [1, 2]
    assert {item["osis"] for item in published["references"]} == {"John.3.16", "Rom.5.8"}


def test_a_document_above_the_ceiling_fails_the_build_naming_the_file(
    tmp_path, project_root, commentary_module
) -> None:
    """Caught here, in the build, rather than hours later in a rejected push."""
    with pytest.raises(DocumentTooLarge) as error:
        write(
            tmp_path,
            project_root,
            commentary_module,
            [entry("Gen.1.1", 1, 1, 1, "x" * 4096)],
            max_document_bytes=1024,
        )
    assert "1.json" in str(error.value)
    assert "ceiling" in str(error.value)


def test_commentary_skips_entries_without_a_bible_coordinate(
    tmp_path, project_root, commentary_module
) -> None:
    record, _ = write(
        tmp_path,
        project_root,
        commentary_module,
        [
            {"key": "Preface", "raw": "front matter", "plain": "front matter", "html": ""},
            entry("Gen.1.1", 1, 1, 1, "Kept."),
        ],
    )
    assert record["entry_count"] == 1


def test_prose_references_inherit_the_commented_book_and_chapter(
    tmp_path, project_root, commentary_module
) -> None:
    write(
        tmp_path,
        project_root,
        commentary_module,
        [
            entry("John.3.16", 43, 3, 16, "Compare ver. 17 and 1:1 with Ro 5:8; 6:23."),
            entry("John.3.17", 43, 3, 17, "Compare ver. 17 and 1:1 with Ro 5:8; 6:23."),
            entry("John.0.0", 43, 0, 0, "The Gospel opens at 1:1."),
        ],
    )
    chapter = json.loads((tmp_path / "testcom/43/3.json").read_text(encoding="utf-8"))
    published = chapter["entries"][0]
    assert published["verses"] == [16, 17]
    assert [(item["text"], item["ref"], item["book"]) for item in published["references"]] == [
        ("ver. 17", "John 3:17", 43),
        ("1:1", "John 1:1", 43),
        ("Ro 5:8", "Romans 5:8", 45),
        ("6:23", "Romans 6:23", 45),
    ]
    introduction = json.loads((tmp_path / "testcom/43/0.json").read_text(encoding="utf-8"))
    assert introduction["entries"][0]["references"] == [
        {"text": "1:1", "ref": "John 1:1", "osis": "John.1.1", "book": 43, "chapter": 1, "verse": 1}
    ]
