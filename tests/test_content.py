from study_builder.content import extract_osis_references, public_content, strip_markup


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


def test_osis_references_are_extracted_from_markup_and_sword_uris() -> None:
    references = extract_osis_references(
        '<reference osisRef="John.1.1">a</reference>', "sword://Bible/Gen.2.3"
    )
    assert references == ["Gen.2.3", "John.1.1"]
