# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_contract import base_records, byte_value, stream

from study_builder.books import BookRegistry
from study_builder.commentaries import CommentaryWriter
from study_builder.content import ContentProjectionError, public_content
from study_builder.contract import ContractError, GetBibleSwordContractReader
from study_builder.dictionaries import DictionaryWriter


@pytest.fixture
def reader(project_root: Path) -> GetBibleSwordContractReader:
    return GetBibleSwordContractReader(
        project_root / "schemas/getbiblesword-ndjson-v1.schema.json",
        "getbiblesword.ndjson/v1",
    )


def normalized_records(
    *, encoding: str | None = "UTF-8", raw: str = "<p>λόγος</p>", plain: str = "λόγος"
) -> list[dict[str, Any]]:
    records = base_records()
    records[0]["producer_version"] = "0.4.0"
    records[2].update(
        normalized_raw=byte_value(raw),
        normalized_stripped=byte_value(plain),
        raw=byte_value(b"legacy source\x92"),
        stripped=byte_value("legacy stripped text"),
        rendered_default=byte_value("<p>legacy rendered text</p>"),
    )
    if encoding is not None:
        records.insert(
            2,
            {
                "name": byte_value("Encoding"),
                "ordinal": 0,
                "type": "config_entry",
                "value": byte_value(encoding),
            },
        )
    return records


def test_verified_normalized_bytes_supply_text_and_preserve_complete_source(reader) -> None:
    records = normalized_records(encoding="Latin-1")
    record = records[-1]
    record["official_attributes"] = [{"name": byte_value("Lemma"), "value": byte_value("G3056")}]
    record["annotation_segments"] = [{"text": byte_value("λ"), "offset": 3}]
    record["future_projection"] = {"source": byte_value(b"\x00\xff")}
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["raw"] == "<p>λόγος</p>"
        assert entry["plain"] == "λόγος"
        assert entry["html"] == ""
        assert public_content(entry, source_type="OSIS") == {"text": "λόγος"}
        assert entry["_getbiblesword"] == {**record, "sequence": len(records) - 1}
    finally:
        exported.close()


@pytest.mark.parametrize("field", ["normalized_raw", "normalized_stripped"])
def test_normalized_bytes_are_strict_utf8_even_without_convenience_text(reader, field) -> None:
    records = normalized_records(encoding="Latin-1")
    records[-1][field] = byte_value(b"caf\xe9")
    with pytest.raises(ContractError, match="UTF-8|UTF8|utf-8"):
        reader.read(io.BytesIO(stream(records)))


@pytest.mark.parametrize("field", ["normalized_raw", "normalized_stripped", "raw", "stripped"])
def test_every_byte_envelope_remains_verified_when_normalized_text_exists(reader, field) -> None:
    records = normalized_records()
    records[-1][field]["sha256"] = "0" * 64
    with pytest.raises(ContractError, match="sha256"):
        reader.read(io.BytesIO(stream(records)))


@pytest.mark.parametrize("field", ["rendered_default", "stripped"])
@pytest.mark.parametrize("state", ["string", "missing", "null"])
def test_available_legacy_projections_require_valid_envelopes_with_normalized_text(
    reader, field, state
) -> None:
    records = normalized_records()
    if state == "missing":
        del records[-1][field]
    else:
        records[-1][field] = "broken" if state == "string" else None
    with pytest.raises(ContractError):
        reader.read(io.BytesIO(stream(records)))


@pytest.mark.parametrize("flag", ["true", 1, None, "missing"])
def test_normalized_entries_require_a_boolean_legacy_projection_flag(reader, flag) -> None:
    records = normalized_records()
    if flag == "missing":
        del records[-1]["projections_available"]
    else:
        records[-1]["projections_available"] = flag
    with pytest.raises(ContractError):
        reader.read(io.BytesIO(stream(records)))


@pytest.mark.parametrize("field", ["rendered_default", "stripped"])
def test_unavailable_legacy_projections_cannot_carry_nonnull_values(reader, field) -> None:
    records = normalized_records()
    records[-1].update(projections_available=False, rendered_default=None, stripped=None)
    records[-1][field] = byte_value("unexpected legacy projection")
    with pytest.raises(ContractError):
        reader.read(io.BytesIO(stream(records)))


def test_normalized_text_is_usable_when_both_legacy_projections_are_unavailable(reader) -> None:
    records = normalized_records()
    records[-1].update(projections_available=False, rendered_default=None, stripped=None)
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["raw"] == "<p>λόγος</p>"
        assert entry["plain"] == "λόγος"
        assert public_content(entry, source_type="OSIS") == {"text": "λόγος"}
        assert entry["_getbiblesword"] == {**records[-1], "sequence": len(records) - 1}
    finally:
        exported.close()


@pytest.mark.parametrize("field", ["normalized_raw", "normalized_stripped"])
def test_normalized_convenience_text_cannot_override_authoritative_bytes(reader, field) -> None:
    records = normalized_records()
    records[-1][field]["utf8"] = "forged convenience text"
    with pytest.raises(ContractError, match="utf8"):
        reader.read(io.BytesIO(stream(records)))


@pytest.mark.parametrize("raw_state", ["missing", "null"])
def test_normalized_stripped_requires_a_nonnull_normalized_raw(reader, raw_state) -> None:
    records = normalized_records()
    if raw_state == "missing":
        del records[-1]["normalized_raw"]
    else:
        records[-1]["normalized_raw"] = None
    with pytest.raises(ContractError):
        reader.read(io.BytesIO(stream(records)))


def test_null_normalization_preserves_warning_but_blocks_legacy_text_fallback(reader) -> None:
    records = normalized_records(encoding="SCSU")
    records[-1]["normalized_raw"] = None
    records[-1]["normalized_stripped"] = None
    records.append(
        {
            "code": "normalization.text_unavailable",
            "message": byte_value("The source cannot be decoded strictly."),
            "severity": "warning",
            "type": "diagnostic",
        }
    )
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["_text_error"]
        assert entry["raw"] == entry["plain"] == entry["html"] == ""
        assert exported.diagnostics[0]["code"] == "normalization.text_unavailable"
        assert exported.diagnostics[0]["message_text"] == "The source cannot be decoded strictly."
        assert exported.footer["success"] is True
        with pytest.raises(ContentProjectionError, match="John 1:1"):
            public_content(entry, source_type="OSIS")
    finally:
        exported.close()


@pytest.mark.parametrize("source_type", ["TEI", "OSIS", "ThML"])
@pytest.mark.parametrize("stripped_state", ["missing", "null"])
def test_source_markup_projects_when_normalized_stripped_is_unavailable(
    reader, source_type, stripped_state
) -> None:
    records = normalized_records(raw="<p>&#x3BB; &amp;alpha;</p>")
    if stripped_state == "missing":
        del records[-1]["normalized_stripped"]
    else:
        records[-1]["normalized_stripped"] = None
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["plain"] == entry["html"] == ""
        assert public_content(entry, source_type=source_type) == {"text": "λ &alpha;"}
    finally:
        exported.close()


def test_normalized_plain_is_not_decoded_as_markup_or_legacy_module_encoding(reader) -> None:
    records = normalized_records(encoding="Latin-1", plain="café &amp; λ")
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        assert public_content(exported.entries[0]) == {"text": "café &amp; λ"}
    finally:
        exported.close()


def test_normalized_utf8_bom_is_retained_as_source_content(reader) -> None:
    records = normalized_records(raw="\ufeff<p>λόγος</p>", plain="\ufeffλόγος")
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["raw"] == "\ufeff<p>λόγος</p>"
        assert entry["plain"] == "\ufeffλόγος"
    finally:
        exported.close()


@pytest.mark.parametrize(
    ("encoding", "key", "expected"),
    [
        # Keys remain authoritative index bytes, not normalized body text. Keep
        # the legacy decoder's values so upgrading does not rename v1 paths.
        ("Latin-1", "café".encode(), "cafÃ©"),
        ("Latin-1", b"caf\xe9\x92", "café’"),
        (None, b"caf\xe9\x92", "café’"),
        ("UTF-8", b"Caf\xe9", "Café"),
        # Easton's original index key differs from its valid normalized body.
        ("UTF-8", b"ABRAHAM\xc2\x80\x99S BOSOM", "ABRAHAM\u0080\u2122S BOSOM"),
        ("UTF-8", "café λόγος".encode(), "café λόγος"),
        ("SCSU", b"G03056", "G03056"),
        ("SCSU", "λόγος".encode(), "λόγος"),
        ("UTF-16", b"John 1:1", "John 1:1"),
        ("UTF-16", "λόγος".encode(), "λόγος"),
    ],
)
def test_normalized_entries_preserve_compatible_keys_without_redecoding_body_text(
    reader, encoding, key, expected
) -> None:
    records = normalized_records(encoding=encoding)
    records[-1]["key"] = byte_value(key)
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["key"] == expected
        assert entry["raw"] == "<p>λόγος</p>"
        assert entry["_getbiblesword"]["raw"] == records[-1]["raw"]
    finally:
        exported.close()


def test_both_normalized_fields_absent_preserves_legacy_projection(reader) -> None:
    records = base_records()
    records[2]["raw"] = byte_value(b"<p>Moses\x92 caf\xc3\xa9</p>")
    records[2]["stripped"] = byte_value(b"Moses\x92 caf\xc3\xa9")
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        entry = exported.entries[0]
        assert entry["raw"] == "<p>Moses’ café</p>"
        assert entry["plain"] == "Moses’ café"
        assert entry["html"] == "<p>Word</p>"
        assert "_text_error" not in entry
        assert public_content(entry, source_type="OSIS") == {"text": "Moses’ café"}
    finally:
        exported.close()


@pytest.mark.parametrize("resource", ["dictionaries", "commentaries"])
@pytest.mark.parametrize(
    ("source_type", "markup"),
    [
        ("TEI", '<entry>&#x3BB;όγος<lb/><ref target="John.1.1">John 1:1</ref></entry>'),
        ("OSIS", '<p>&#x3BB;όγος<lb/><reference osisRef="John.1.1">John 1:1</reference></p>'),
        ("ThML", '<p>&#x3BB;όγος<br/><scripRef passage="John 1:1">John 1:1</scripRef></p>'),
    ],
)
def test_normalized_reader_to_writer_preserves_public_v1_text_and_references(
    reader,
    tmp_path,
    project_root,
    reference_engine,
    greek_dictionary_module,
    commentary_module,
    resource,
    source_type,
    markup,
) -> None:
    records = normalized_records(encoding="SCSU", raw=markup)
    records[-1]["normalized_stripped"] = None
    records[-1]["raw"] = byte_value(b"\x0e\xff\xfe opaque source bytes")
    if resource == "dictionaries":
        records[-1]["key"] = byte_value("03056")
        records[1]["classification"] = "dictionary"
        records[1]["name"] = byte_value("StrongsGreek")
        records[1]["sword_type"] = byte_value("Lexicons / Dictionaries")
        module = greek_dictionary_module
        writer_type = DictionaryWriter
    else:
        module = commentary_module
        writer_type = CommentaryWriter
    module = replace(module, fields={**module.fields, "sourcetype": (source_type,)})
    exported = reader.read(io.BytesIO(stream(records)))
    try:
        writer = writer_type(
            tmp_path,
            BookRegistry(project_root / "conf/book_registry.json"),
            project_root / "schemas",
            references=reference_engine,
        )
        writer.write(module, exported)
    finally:
        exported.close()
    if resource == "dictionaries":
        document = json.loads((tmp_path / "strongsgreek/G3056.json").read_text())
        complete = json.loads((tmp_path / "strongsgreek.json").read_text())
        assert complete["entries"][0] == document
        assert document["id"] == "G3056"
        assert document["aliases"] == ["03056", "G3056"]
        assert document["schema"] == "getbible-dictionary-entry-v1"
    else:
        chapter = json.loads((tmp_path / "testcom/43/1.json").read_text())
        complete = json.loads((tmp_path / "testcom.json").read_text())
        assert complete["books"][0]["chapters"][0] == chapter
        document = chapter["entries"][0]
        assert document["osis"] == "John.1.1"
        assert document["verse"] == 1
        assert chapter["schema"] == "getbible-commentary-chapter-v1"
    assert document["text"] == "λόγος\nJohn 1:1"
    assert "html" not in document
    assert "normalized_raw" not in document
    assert "_getbiblesword" not in document
    assert document["references"] == [
        {
            "ref": "John 1:1",
            "osis": "John.1.1",
            "book": 43,
            "chapter": 1,
            "verse": 1,
            "text": "John 1:1",
        }
    ]
