import pytest

from study_builder.content import (
    ContentProjectionError,
    clean_text,
    extract_markup_references,
    extract_osis_references,
    normalize_text,
    public_content,
    strip_markup,
    tei_text,
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
    assert public_content(entry, source_type="TEI") == {"text": "First\nSecond"}
    assert public_content(
        {"raw": "", "plain": "", "html": "<p>Rendered</p>"}, source_type="ThML"
    ) == {"text": "Rendered"}


def test_tei_preserves_dictionary_fields_senses_and_scripture_labels() -> None:
    raw = (
        '<entryFree n="109"><orth>ἀήρ</orth><lb/>'
        '<orth type="writing">ajhvr</orth> <orth type="trans">aer</orth> '
        "<pron>{ah-ayr'}</pron><p>Air: &#x03B1; &#945; &amp; &lt;literal&gt;.</p>"
        '<sense n="1"><def>The air.</def> '
        '<ref osisRef="John.3.8">John<lb/>3:8</ref></sense>'
        '<sense n="2"><def>The sky.</def><note>Usage note.</note></sense></entryFree>'
    )
    content = public_content({"raw": raw, "plain": "109. flattened"}, source_type=" TEI ")
    assert content == {
        "text": "109 ἀήρ\najhvr aer {ah-ayr'}\nAir: α α & <literal>.\n\n"
        "1 The air. John\n3:8\n\n2 The sky. [Usage note.]"
    }
    assert extract_markup_references(raw, source_type="TEI") == [
        MarkupReference("osis", "John.3.8", "John 3:8")
    ]


def test_tei_source_newlines_are_whitespace_and_explicit_breaks_are_preserved() -> None:
    assert normalize_text(tei_text("<def>wrapped\r\n definition\n continues</def><lb/>next")) == (
        "wrapped definition continues\nnext"
    )


def test_tei_lists_tables_and_unknown_elements_keep_their_text() -> None:
    raw = (
        '<head>Forms</head><list><item n="a">First</item><item n="b">Second</item></list>'
        "<table><row><cell>α</cell><cell>a</cell></row>"
        "<row><cell>β</cell><cell>b</cell></row></table>"
        '<new-element>Retained <hi rend="italic">words</hi>.</new-element>'
        "<tr>translation</tr> follows"
    )
    lines = normalize_text(tei_text(raw)).splitlines()
    assert [line for line in lines if line] == [
        "Forms",
        "a First",
        "b Second",
        "α a",
        "β b",
        "Retained words.translation follows",
    ]


def test_tei_namespaces_notes_and_cdata_do_not_discard_content() -> None:
    raw = (
        "<tei:entry><tei:p>Text<tei:note/> continues.</tei:p>"
        "<tei:note>Actual note</tei:note><tei:lb/>"
        "<![CDATA[literal &amp; <tag> content]]><tei:script/> survives"
        "<tei:style>suppressed</tei:style><!-- hidden --></tei:entry>"
    )
    assert public_content({"raw": raw}, source_type="TEI") == {
        "text": "Text continues.\n[Actual note]\nliteral &amp; <tag> content survives"
    }


def test_tei_empty_note_cleanup_never_removes_literal_empty_brackets() -> None:
    raw = (
        "<def>Use [] for an empty list.</def> "
        "<![CDATA[literal [] text]]><note><note> </note></note>"
    )
    assert public_content({"raw": raw}, source_type="TEI") == {
        "text": "Use [] for an empty list. literal [] text"
    }


def test_tei_visible_attribute_values_follow_xml_entity_rules() -> None:
    raw = '<sense n="&#128;">first</sense><sense n="&notit; &amp;#945;">second</sense>'
    assert public_content({"raw": raw}, source_type="TEI") == {
        "text": "\x80 first\n\n&notit; &#945; second"
    }


@pytest.mark.parametrize("source_type", ["TEI", "ThML", "OSIS"])
def test_markup_entities_are_decoded_once(source_type: str) -> None:
    raw = "<p>&#x03B1; &#945; &amp;#x03B2; &amp;lt;literal&amp;gt;</p>"
    assert public_content({"raw": raw}, source_type=source_type) == {
        "text": "α α &#x03B2; &lt;literal&gt;"
    }


def test_tei_preserves_unknown_entities_and_xml_numeric_codepoints() -> None:
    raw = "<def>&notit; &Alpha; &#128; &#x110000; &#xD800; &#0; &#10;next</def>"
    assert public_content({"raw": raw}, source_type="TEI") == {
        "text": "&notit; Α \x80 &#x110000; &#xD800; &#0;\nnext"
    }


def test_plain_text_is_never_reinterpreted_as_entities_or_markup() -> None:
    text = "literal &#x4e2d; &notit; &amp; <w>ordinary text</w>"
    assert clean_text(text) == text
    assert public_content({"plain": text}, source_type="Plain") == {"text": text}


def test_tei_projection_preserves_every_source_language() -> None:
    text = "ὅς 包括阴性的 he שלום"
    assert public_content({"raw": f"<pron>{text}</pron>"}, source_type="TEI") == {"text": text}


@pytest.mark.parametrize("source_type", ["TEI", "OSIS"])
def test_rendered_fallback_can_still_be_source_markup(source_type: str) -> None:
    assert public_content(
        {"raw": "", "plain": "", "html": "<p>α<lb/>β &#945;</p>"}, source_type=source_type
    ) == {"text": "α\nβ α"}


@pytest.mark.parametrize("source_type", ["TEI", "ThML", "OSIS", ""])
def test_empty_or_suppressed_source_is_not_a_projection_failure(source_type: str) -> None:
    raw = "<p> </p><lb/><script>hidden</script><style>hidden</style><!-- hidden -->"
    assert public_content({"raw": raw}, source_type=source_type) == {"text": ""}


@pytest.mark.parametrize("source_type", ["TEI", "OSIS"])
def test_namespaced_suppressed_source_does_not_trigger_content_loss(source_type: str) -> None:
    raw = "<tei:style>hidden</tei:style><osis:script>hidden</osis:script>"
    assert public_content({"raw": raw}, source_type=source_type) == {"text": ""}


def test_osis_metadata_only_notes_are_not_lost_visible_content() -> None:
    raw = '<osis:note type="strongsMarkup">internal metadata</osis:note>'
    assert public_content({"raw": raw}, source_type="OSIS") == {"text": ""}


def test_nonempty_source_cannot_be_silently_omitted_when_projections_are_empty() -> None:
    with pytest.raises(ContentProjectionError, match="Entry 'lost'.*GBF.*source text"):
        public_content(
            {"key": "lost", "raw": "A definition", "plain": "", "html": ""}, source_type="GBF"
        )


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
        '<scripRef passage="Nu 21:6-9"><b>Nu</b> 21:6-9</scripRef> <scripRef>Mt 5:3</scripRef> '
        '<scripRef parsed="|Gen.11.9|">Ge 11:9</scripRef> '
        '<scripRef parsed="|Gen|4|2|4|2;|Gen|4|8|4|8">Ge 4:2, 8</scripRef>'
    )
    assert extract_markup_references(raw) == [
        MarkupReference("osis", "Gen.4.2", "Gen. 4:2"),
        MarkupReference("osis", "Bible:Ps.23.1", "Ps 23:1"),
        MarkupReference("osis", "Ps.23.2", "v. 2"),
        # One reference per tag: the passage, as SWORD renders it, over the parsed
        # form; the parsed form only where it is all there is and really is OSIS.
        MarkupReference("passage", "Ge 11:7", "Ge 11:7"),
        MarkupReference("passage", "Nu 21:6-9", "Nu 21:6-9"),
        MarkupReference("passage", "Mt 5:3", "Mt 5:3"),
        MarkupReference("osis", "Gen.11.9", "Ge 11:9"),
        MarkupReference("passage", "Ge 4:2, 8", "Ge 4:2, 8"),
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
    # The rendered form describes the same tags as the source markup, so it is read
    # only when the source carries no reference at all.
    raw = '<reference osisRef="Rom.5.8">Rom. 5:8</reference>'
    assert extract_markup_references(raw, html) == [MarkupReference("osis", "Rom.5.8", "Rom. 5:8")]
    assert len(extract_markup_references("<p>Rom. 5:8</p>", html)) == 3


def test_markup_references_count_the_display_text_before_them() -> None:
    raw = (
        'Compare Gen 1:1 and <scripRef passage="Ex 1:1">1:1</scripRef>, then '
        '<scripRef passage="Le 1:1">1:1</scripRef> with <b>1:1</b> and '
        '<scripRef passage="Nu 1:1">1:1</scripRef>'
    )
    assert [(item.value, item.occurrence) for item in extract_markup_references(raw)] == [
        ("Ex 1:1", 1),
        ("Le 1:1", 2),
        ("Nu 1:1", 4),
    ]


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


@pytest.mark.parametrize(
    ("source_type", "tag", "attribute"),
    [
        ("TEI", "tei:ref", 'target="Bible:John.3.16"'),
        ("OSIS", "osis:reference", 'osisRef="John.3.16"'),
    ],
)
def test_namespaced_markup_references_keep_displayed_labels(
    source_type: str, tag: str, attribute: str
) -> None:
    raw = f"<{tag} {attribute}>John<lb/>3:16</{tag}>"
    assert extract_markup_references(raw, source_type=source_type) == [
        MarkupReference("osis", "John.3.16", "John 3:16")
    ]


def test_literal_or_hidden_markup_is_not_extracted_as_a_reference() -> None:
    raw = (
        '<![CDATA[<ref osisRef="Gen.1.1">1:1</ref>]]>'
        '<script><ref osisRef="Lev.1.1">1:1</ref></script>'
        '<tei:style><ref osisRef="Num.1.1">1:1</ref></tei:style>'
        '<tei:ref target="Bible:Exod.1.1">1:1</tei:ref>'
        '<script><ref osisRef="Deut.1.1">1:1</ref>'
    )
    assert extract_markup_references(raw, source_type="TEI") == [
        MarkupReference("osis", "Exod.1.1", "1:1", 1)
    ]


def test_osis_metadata_notes_do_not_publish_hidden_references() -> None:
    raw = (
        '<note type="x-strongsMarkup"><note>metadata</note>'
        '<reference osisRef="Gen.1.1">1:1</reference></note>'
        '<note><reference osisRef="Exod.1.1">1:1</reference></note>'
    )
    assert extract_markup_references(raw, source_type="OSIS") == [
        MarkupReference("osis", "Exod.1.1", "1:1")
    ]
