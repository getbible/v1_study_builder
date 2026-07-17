from study_builder.content import clean_html, extract_osis_references, public_content


def test_html_sanitizer_removes_scripts_and_unsafe_links() -> None:
    cleaned = clean_html(
        "<p>Hello <strong>world</strong></p><script>alert(1)</script>"
        '<a href="javascript:alert(2)">bad</a>'
    )
    assert "<script" not in cleaned
    assert "javascript:" not in cleaned
    assert "<strong>world</strong>" in cleaned


def test_reference_extraction_deduplicates() -> None:
    assert extract_osis_references(
        'osisRef="Gen.1.1"', '<a href="sword://Gen.1.1">Genesis</a> Matt.5.3'
    ) == ["Gen.1.1", "Matt.5.3"]


def test_structural_html_without_text_is_not_public_content() -> None:
    assert public_content({"plain": "", "html": '<span class="marker"></span><br>'}) == {"text": ""}
