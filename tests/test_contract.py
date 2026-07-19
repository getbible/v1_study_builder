# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from study_builder.contract import ContractError, GetBibleSwordContractReader
from study_builder.entries import EntrySpool


def byte_value(value: str | bytes) -> dict[str, Any]:
    payload = value.encode() if isinstance(value, str) else value
    result: dict[str, Any] = {
        "base64": base64.b64encode(payload).decode("ascii"),
        "encoding": "base64",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    with contextlib.suppress(UnicodeDecodeError):
        result["utf8"] = payload.decode("utf-8", errors="strict")
    return result


def base_records() -> list[dict[str, Any]]:
    return [
        {
            "artifact_chunk_size": 1048576,
            "command": "extract",
            "contract": "getbiblesword.ndjson/v1",
            "contract_version": 1,
            "deterministic": True,
            "producer": "getBibleSword",
            "producer_version": "0.1.0",
            "sword_version": "1.9.0",
            "type": "header",
        },
        {
            "classification": "commentary",
            "description": byte_value("Test Commentary"),
            "direction": {"code": 0, "name": "ltr"},
            "driver": byte_value("RawCom"),
            "encoding": {"code": 2, "name": "utf8"},
            "language": byte_value("en"),
            "markup": {"code": 7, "name": "osis"},
            "name": byte_value("TestCom"),
            "sword_type": byte_value("Commentaries"),
            "type": "module",
        },
        {
            "annotation_segments": [],
            "key": byte_value("John 1:1"),
            "official_attributes": [],
            "ordinal": 0,
            "projections_available": True,
            "raw": byte_value("<p>Word</p>"),
            "rendered_default": byte_value("<p>Word</p>"),
            "scope": {
                "book": 4,
                "book_abbreviation": byte_value("John"),
                "book_name": byte_value("John"),
                "chapter": 1,
                "index": 1,
                "intro_scope": "verse",
                "osis_reference": byte_value("John.1.1"),
                "suffix": 0,
                "testament": 2,
                "type": "verse_key",
                "verse": 1,
                "versification": byte_value("KJV"),
            },
            "stripped": byte_value("Word"),
            "type": "entry",
        },
    ]


def stream(records: list[dict[str, Any]], success: bool = True) -> bytes:
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    lines: list[bytes] = []
    for sequence, source in enumerate(records):
        record = {**source, "sequence": sequence}
        line = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        digest.update(line)
        counts[str(record["type"])] += 1
        lines.append(line)
    diagnostic_counts: Counter[str] = Counter(
        str(record.get("severity")) for record in records if record.get("type") == "diagnostic"
    )
    footer = {
        "artifact_bytes": sum(
            int(record.get("size", 0)) for record in records if record.get("type") == "artifact_end"
        ),
        "artifacts": sum(record.get("type") == "artifact_begin" for record in records),
        "counts": dict(sorted(counts.items())),
        "diagnostics": {
            name: diagnostic_counts.get(name, 0) for name in ("error", "info", "warning")
        },
        "entries": sum(record.get("type") == "entry" for record in records),
        "sequence": len(records),
        "stream_sha256": digest.hexdigest(),
        "success": success,
        "type": "footer",
    }
    lines.append(json.dumps(footer, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return b"".join(lines)


@pytest.fixture
def reader(project_root: Path) -> GetBibleSwordContractReader:
    return GetBibleSwordContractReader(
        project_root / "schemas/getbiblesword-ndjson-v1.schema.json",
        "getbiblesword.ndjson/v1",
    )


def test_valid_stream_maps_authoritative_bytes(reader: GetBibleSwordContractReader) -> None:
    exported = reader.read(io.BytesIO(stream(base_records())))
    assert isinstance(exported.entries, EntrySpool)
    assert exported.metadata["classification"] == "commentary"
    assert exported.metadata["name"] == "TestCom"
    assert exported.entries[0]["key"] == "John 1:1"
    assert exported.entries[0]["plain"] == "Word"
    assert exported.entries[0]["verse"]["osis"] == "John.1.1"
    assert [entry["key"] for entry in exported.entries] == ["John 1:1"]
    exported.close()
    with pytest.raises(RuntimeError, match="not available"):
        list(exported.entries)


def test_artifact_chunks_are_reassembled_and_verified(
    reader: GetBibleSwordContractReader,
) -> None:
    payload = b"module bytes"
    records = base_records()
    records.extend(
        [
            {
                "artifact_id": 0,
                "file_type": "regular",
                "mode": 420,
                "path": byte_value("modules/test.dat"),
                "role": "module_data",
                "size_expected": len(payload),
                "type": "artifact_begin",
            },
            {
                "artifact_id": 0,
                "data": byte_value(payload),
                "index": 0,
                "type": "artifact_chunk",
            },
            {
                "artifact_id": 0,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "stable": True,
                "type": "artifact_end",
            },
        ]
    )
    exported = reader.read(io.BytesIO(stream(records)))
    assert exported.footer["artifact_bytes"] == len(payload)


def test_rejects_bad_stream_digest(reader: GetBibleSwordContractReader) -> None:
    payload = bytearray(stream(base_records()))
    marker = b'"stream_sha256":"'
    position = payload.rfind(marker) + len(marker)
    payload[position] = ord("0") if payload[position] != ord("0") else ord("1")
    with pytest.raises(ContractError, match="stream digest"):
        reader.read(io.BytesIO(payload))


def test_rejects_bad_byte_envelope(reader: GetBibleSwordContractReader) -> None:
    records = base_records()
    records[2]["key"] = {**records[2]["key"], "size": 99}
    with pytest.raises(ContractError, match="size does not match"):
        reader.read(io.BytesIO(stream(records)))


def test_rejects_unsuccessful_footer(reader: GetBibleSwordContractReader) -> None:
    with pytest.raises(ContractError, match="unsuccessful"):
        reader.read(io.BytesIO(stream(base_records(), success=False)))


def test_rejects_error_diagnostic_even_with_success_footer(
    reader: GetBibleSwordContractReader,
) -> None:
    records = base_records()
    records.append(
        {
            "code": "synthetic.error",
            "message": byte_value("Synthetic failure"),
            "severity": "error",
            "type": "diagnostic",
        }
    )
    with pytest.raises(ContractError, match="error diagnostic") as captured:
        reader.read(io.BytesIO(stream(records)))
    assert "synthetic.error: Synthetic failure" in str(captured.value)
