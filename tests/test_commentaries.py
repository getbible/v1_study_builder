import json

from study_builder.books import BookRegistry
from study_builder.commentaries import CommentaryWriter
from study_builder.models import NativeExport


class OnePassEntries:
    def __init__(self, entries):
        self.entries = entries
        self.iterated = False

    def __iter__(self):
        assert not self.iterated, "commentary entries were loaded or traversed more than once"
        self.iterated = True
        yield from self.entries

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, index):
        return self.entries[index]


def test_commentary_matches_v3_book_chapter_verse_contract(
    tmp_path, project_root, commentary_module
) -> None:
    export = NativeExport(
        metadata={"record_type": "module"},
        entries=[
            {
                "record_type": "entry",
                "key": "Genesis 1:1",
                "raw": '<reference osisRef="John.1.1">John 1:1</reference>',
                "plain": "A comment on creation.",
                "html": "<p>A comment on <strong>creation</strong>.</p>",
                "verse": {
                    "osis": "Gen.1.1",
                    "testament": 1,
                    "book": 1,
                    "chapter": 1,
                    "verse": 1,
                },
            }
        ],
    )
    writer = CommentaryWriter(
        tmp_path,
        BookRegistry(project_root / "conf/book_registry.json"),
        project_root / "schemas/commentary-chapter.schema.json",
    )
    summary = writer.write(commentary_module, export)
    chapter_path = tmp_path / "testcom/1/1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    assert summary["chapter_url_template"] == "{book}/{chapter}.json"
    assert (chapter["book"], chapter["chapter"]) == (1, 1)
    assert chapter["entries"][0]["verse"] == 1
    assert chapter["entries"][0]["anchor"]["osis"] == "Gen.1.1"
    assert chapter["entries"][0]["references"][0]["book"] == 43


def test_commentary_streams_one_chapter_at_a_time(
    tmp_path, project_root, commentary_module
) -> None:
    entries = OnePassEntries(
        [
            {
                "key": "Genesis 1:1",
                "raw": "First chapter",
                "plain": "First chapter",
                "html": "",
                "verse": {
                    "osis": "Gen.1.1",
                    "testament": 1,
                    "book": 1,
                    "chapter": 1,
                    "verse": 1,
                },
            },
            {
                "key": "Genesis 2:1",
                "raw": "Second chapter",
                "plain": "Second chapter",
                "html": "",
                "verse": {
                    "osis": "Gen.2.1",
                    "testament": 1,
                    "book": 1,
                    "chapter": 2,
                    "verse": 1,
                },
            },
        ]
    )
    export = NativeExport(metadata={}, entries=entries)

    summary = CommentaryWriter(
        tmp_path,
        BookRegistry(project_root / "conf/book_registry.json"),
        project_root / "schemas/commentary-chapter.schema.json",
    ).write(commentary_module, export)

    assert entries.iterated
    assert summary["entry_count"] == 2
    assert (tmp_path / "testcom/1/1.json").is_file()
    assert (tmp_path / "testcom/1/2.json").is_file()
