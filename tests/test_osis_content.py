# SPDX-License-Identifier: GPL-2.0-only
"""Content fidelity at the OSIS-to-plain-text boundary."""

import pytest

from study_builder.content import normalize_text
from study_builder.osis import osis_text
from study_builder.xmltext import decode_xml_entities, xml_attributes


def text(source: str, **kwargs: int | None) -> str:
    return normalize_text(osis_text(source, **kwargs))


def test_structure_keeps_words_and_commentary_sections_separate() -> None:
    source = (
        "<title>Heading</title><p>First<lb/>second</p><p>Next paragraph.</p>"
        "<list><item>one</item><item>two</item></list>"
        "<table><row><cell>left</cell><cell>right</cell></row></table>"
    )
    assert text(source) == ("Heading\n\nFirst\nsecond\n\nNext paragraph.\n\none\ntwo\n\nleft right")


def test_poetry_and_milestoned_paragraphs_keep_their_boundaries() -> None:
    source = (
        '<div type="paragraph" sID="p1"/>first'
        '<div type="paragraph" eID="p1"/><div type="paragraph" sID="p2"/>second'
        '<milestone type="line"/>third<milestone type="x-p"/>fourth'
        '<lg><l sID="l1"/>line one<l eID="l1"/>'
        '<l sID="l2"/>line two<l eID="l2"/></lg>'
    )
    assert text(source).splitlines() == [
        "first",
        "",
        "second",
        "third",
        "fourth",
        "line one",
        "line two",
    ]


def test_entities_unicode_cdata_and_unknown_inline_content_are_preserved_once() -> None:
    source = (
        "<p>&#x03B1; &#945; &#x1F00; &Alpha; &amp; &amp;#945; &lt;word&gt; "
        "<seg>中文</seg><custom>tail</custom> e\u0301 "
        "<![CDATA[&amp; <literal>]]> &unknown;</p>"
    )
    assert text(source) == "α α ἀ Α & &#945; <word> 中文tail e\u0301 &amp; <literal> &unknown;"
    # XML references must not receive HTML's Windows-1252 remapping.
    assert osis_text("&#128;") == "\x80"
    assert osis_text("a&#10;b") == "a\nb"


def test_invalid_xml_character_references_are_retained_for_diagnosis() -> None:
    source = "&#0; &#1; &#xD800; &#xFFFE; &#1114112;"
    assert osis_text(source) == source


def test_visible_attributes_follow_the_same_exact_entity_rules_as_body_text() -> None:
    source = (
        '<q marker="&#128;">&#128;</q><w gloss="&notit; &amp;#945; &#x03B1;">word</w>'
        '<milestone type="cQuote" marker="&#xD800;"/>'
    )
    assert osis_text(source) == "\x80\x80\x80word <&notit; &#945; α>&#xD800;"


def test_xml_attribute_values_and_literal_entity_text_are_decoded_only_once() -> None:
    source = '<osis:item N="&amp;#128; &notit; &#128; &quot;x&quot;" xml:lang=\'en\' empty=""/>'
    assert xml_attributes(source) == {
        "n": '&#128; &notit; \x80 "x"',
        "xml:lang": "en",
        "empty": "",
    }
    assert decode_xml_entities("&Alpha; &amp;alpha; &notit; &alpha &amp") == (
        "Α &alpha; &notit; &alpha &amp"
    )


def test_reference_labels_inline_tails_and_note_bodies_survive() -> None:
    source = (
        '<p>See <reference osisRef="John.1.1">John 1:1</reference>, then '
        '<note type="crossReference"><reference osisRef="Gen.1.1">Gen. 1:1</reference>'
        " with a note.</note>continue<note/>.</p>"
    )
    assert text(source) == "See John 1:1, then [Gen. 1:1 with a note.] continue."


def test_namespaced_elements_and_nested_notes_keep_the_same_visible_text() -> None:
    source = (
        '<osis:p xmlns:osis="http://www.bibletechnologies.net/2003/OSIS/namespace">'
        "A<osis:lb/>B<osis:note>outer <osis:note>inner</osis:note> tail</osis:note>C"
        "</osis:p>"
    )
    assert text(source) == "A\nB [outer [inner] tail] C"


@pytest.mark.parametrize("kind", ["strongsMarkup", "x-strongsMarkup"])
def test_internal_strongs_metadata_notes_are_not_visible_footnotes(kind: str) -> None:
    assert (
        text(
            f'<p>Before<note type="{kind}"><w lemma="strong:G3056">internal</w></note>'
            '<note type="explanation">visible</note>after</p>'
        )
        == "Before [visible] after"
    )
    assert text(f'<note type="{kind}">internal metadata</note>') == ""


def test_word_annotations_follow_sword_plain_text_conventions() -> None:
    source = (
        '<w xlit="betacode:logos" gloss="word" lemma="strong:G3056 strong:H0430" '
        'morph="robinson:N-NSM strongMorph:TG5656" POS="gram:noun">λόγος</w>'
        ' <w lemma="strong:G3588"/>.'
    )
    assert text(source) == "λόγος <logos> <word> <G3056> <H0430> (N-NSM) (5656) <noun> <G3588>."


@pytest.mark.parametrize("testament,prefix", [(None, ""), (1, "H"), (2, "G"), (0, "")])
def test_bare_strongs_lemma_uses_only_verified_testament_context(
    testament: int | None, prefix: str
) -> None:
    assert text('<w lemma="strong:430">word</w>', testament=testament) == f"word <{prefix}430>"


def test_explicit_quote_markers_emphasis_and_divine_names_are_retained() -> None:
    assert (
        text(
            '<q marker="&quot;">A <hi type="italic">word</hi></q> '
            '<divineName>Lord</divineName> <q sID="q1" marker="«"/>speech'
            '<q eID="q1" marker="»"/>'
        )
        == '"A * word *" LORD «speech»'
    )


def test_empty_source_structures_and_noncontent_do_not_create_text() -> None:
    assert text("<p/><lb/><note/><note> </note><hi/> <!-- comment -->") == ""
    assert text("<p>word<script>ignored</script><style>ignored</style> tail</p>") == "word tail"


def test_fragment_closure_does_not_discard_nested_annotation_text() -> None:
    assert text('<p>First\nline<lb type="x-optional"/> end<note>A <hi>note') == (
        "First line end [A * note *]"
    )
