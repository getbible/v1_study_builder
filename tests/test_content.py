from study_builder.content import (
    extract_markup_references,
    extract_osis_references,
    normalize_text,
    public_content,
    strip_markup,
    thml_text,
)
from study_builder.references import MarkupReference


def test_markup_stripper_drops_scripts_and_keeps_readable_text() -> None:
    text = strip_markup(
        '<p>Safe <strong>text</strong></p><script>alert("x")</script>'
        '<a href="javascript:alert(1)">link</a>'
    )
    assert "alert" not in text
    assert "javascript" not in text
    assert "Safe" in text and "text" in text and "link" in text


def test_public_content_publishes_text_only() -> None:
    content = public_content({"plain": "A word", "html": "<p>A <em>word</em></p>"})
    assert content == {"text": "A word"}


def test_public_content_falls_back_to_markup_when_stripped_text_is_empty() -> None:
    content = public_content({"plain": "", "html": "<p>Only in the rendered form</p>"})
    assert content == {"text": "Only in the rendered form"}


def test_structural_markup_without_text_is_not_public_content() -> None:
    assert public_content({"plain": "", "html": '<span class="marker"></span><br>'}) == {"text": ""}


def test_text_is_normalised_to_one_space_and_trimmed_lines() -> None:
    assert normalize_text("ABEL.   1. Son of Adam\n \t* References\n\n\n\n 2. A stone \r\n") == (
        "ABEL. 1. Son of Adam\n* References\n\n2. A stone"
    )
    assert normalize_text("a\u2003b\u3000c\x0cd") == "a b c d"
    assert public_content({"plain": "26.  ἀγάπη agape\n from 25"}) == {
        "text": "26. ἀγάπη agape\nfrom 25"
    }


def test_thml_text_keeps_breaks_and_swords_conventions() -> None:
    raw = (
        "<b>* Verses 1-8 *</b>&nbsp;&nbsp; Nicodemus came.<p>Second paragraph "
        '<scripRef passage="Nu 21:6-9">Nu 21:6-9</scripRef> ends.</p><br>- God condemns\n'
        'Ge 11:7<br>- Christ <sync type="Strongs" value="G3056"/> <note>a note</note> more.'
        "<ul><li>one</li><li>two</li></ul><table><tr><td>a</td><td>b</td></tr></table>"
        '<script>alert(1)</script><sync type="morph" value="V-PAI"/>'
    )
    assert normalize_text(thml_text(raw)) == (
        "* Verses 1-8 * Nicodemus came.\nSecond paragraph Nu 21:6-9 ends.\n\n- God condemns "
        "Ge 11:7\n- Christ <G3056> [a note] more.\n\none\ntwo\n\na b\n(V-PAI)"
    )


def test_thml_modules_are_projected_from_their_source_not_the_flattened_form() -> None:
    entry = {"raw": "First<br>Second", "plain": "First Second", "html": ""}
    assert public_content(entry, source_type="ThML") == {"text": "First\nSecond"}
    assert public_content(entry, source_type="TEI") == {"text": "First Second"}
    assert public_content(
        {"raw": "", "plain": "", "html": "<p>Rendered</p>"}, source_type="ThML"
    ) == {"text": "Rendered"}


def test_osis_references_are_extracted_from_markup_and_sword_uris() -> None:
    references = extract_osis_references(
        '<reference osisRef="John.1.1">a</reference>', "sword://Bible/Gen.2.3"
    )
    assert references == ["Gen.2.3", "John.1.1"]


def test_markup_references_carry_their_target_and_display_text() -> None:
    raw = (
        '<reference osisRef="Gen.4.2">Gen. 4:2</reference> '
        '<ref osisRef="Bible:Ps.23.1">Ps 23:1</ref> <ref target="Bible:Ps.23.2">v. 2</ref> '
        '<ref target="Easton:ZIN">Zin</ref> '
        '<scripRef parsed="|Gen.11.7|" passage="Ge 11:7">Ge 11:7</scripRef> '
        '<scripRef passage="Nu 21:6-9"><b>Nu</b> 21:6-9</scripRef> <scripRef>Mt 5:3</scripRef>'
    )
    assert extract_markup_references(raw) == [
        MarkupReference("osis", "Gen.4.2", "Gen. 4:2"),
        MarkupReference("osis", "Bible:Ps.23.1", "Ps 23:1"),
        MarkupReference("osis", "Ps.23.2", "v. 2"),
        MarkupReference("osis", "Gen.11.7", "Ge 11:7"),
        MarkupReference("passage", "Nu 21:6-9", "Nu 21:6-9"),
        MarkupReference("passage", "Mt 5:3", "Mt 5:3"),
    ]


def test_rendered_anchors_spell_every_family_the_same_way() -> None:
    html = (
        '<a href="passagestudy.jsp?action=showRef&type=scripRef&value=Gen+1%3A1&module=">'
        "Gen 1:1</a> and "
        '<a href="passagestudy.jsp?action=showRef&amp;type=scripRef&amp;value=John.3.16-John.3.17'
        '&amp;module=">John 3:16, 17</a> <a href="sword://Bible/Rom.5.8">Rom. 5:8</a> '
        '<a href="sword://Easton/ZIN">Zin</a> '
        '<a href="passagestudy.jsp?action=showNote&value=1">*n</a>'
    )
    assert extract_markup_references(html) == [
        MarkupReference("passage", "Gen 1:1", "Gen 1:1"),
        MarkupReference("osis", "John.3.16-John.3.17", "John 3:16, 17"),
        MarkupReference("osis", "Rom.5.8", "Rom. 5:8"),
    ]
    # The same reference in both forms is listed once.
    raw = '<reference osisRef="Rom.5.8">Rom. 5:8</reference>'
    assert len(extract_markup_references(raw, html)) == 3


def test_empty_reference_tags_comments_and_order_are_read_as_the_document_has_them() -> None:
    raw = (
        '<!-- <scripRef passage="Gen 9:9">x</scripRef> -->'
        'the flood <a href="sword://Bible/Gen.7.1">here</a> then '
        '<scripRef passage="Gen 1:1"/> and <scripRef passage="Exod 2:1">Exod 2:1</scripRef> '
        '<a href="sword://Bible/John 3:16">John 3:16</a> '
        '<scripRef passage="Gen 3:1">Gen<br/>3:1<note>n</note></scripRef>'
    )
    assert extract_markup_references(raw) == [
        MarkupReference("osis", "Gen.7.1", "here"),
        MarkupReference("passage", "Gen 1:1", ""),
        MarkupReference("passage", "Exod 2:1", "Exod 2:1"),
        MarkupReference("passage", "John 3:16", "John 3:16"),
        MarkupReference("passage", "Gen 3:1", "Gen 3:1 [n]"),
    ]
