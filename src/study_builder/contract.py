# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from jsonschema import Draft202012Validator

from study_builder.models import NativeExport
from study_builder.util import read_json

MAX_RECORD_BYTES = 64 * 1024 * 1024
RECORD_TYPES = {
    "header",
    "module",
    "config_source",
    "config_entry",
    "entry",
    "artifact_begin",
    "artifact_chunk",
    "artifact_end",
    "diagnostic",
    "footer",
}


class ContractError(RuntimeError):
    pass


@dataclass
class _ArtifactState:
    file_type: str
    digest: Any
    size: int = 0
    next_index: int = 0
    expected_size: int | None = None


def decode_byte_value(value: Any, context: str) -> bytes:
    if not isinstance(value, dict):
        raise ContractError(f"{context} is not a getbiblesword byte value")
    required = {"base64", "encoding", "sha256", "size"}
    missing = sorted(required - value.keys())
    if missing:
        raise ContractError(f"{context} byte value is missing: {', '.join(missing)}")
    if value.get("encoding") != "base64":
        raise ContractError(f"{context} has an unsupported byte encoding")
    encoded = value.get("base64")
    if not isinstance(encoded, str):
        raise ContractError(f"{context}.base64 is not a string")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise ContractError(f"{context}.base64 is invalid") from error
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size != len(payload):
        raise ContractError(f"{context}.size does not match its decoded bytes")
    expected_hash = value.get("sha256")
    actual_hash = hashlib.sha256(payload).hexdigest()
    if not isinstance(expected_hash, str) or not hmac.compare_digest(
        actual_hash, expected_hash.casefold()
    ):
        raise ContractError(f"{context}.sha256 does not match its decoded bytes")
    if "utf8" in value:
        try:
            decoded = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ContractError(f"{context}.utf8 is present for invalid UTF-8 bytes") from error
        if value["utf8"] != decoded:
            raise ContractError(f"{context}.utf8 does not match its decoded bytes")
    return payload


def _validate_byte_values(value: Any, context: str) -> None:
    if isinstance(value, dict):
        if "base64" in value or value.get("encoding") == "base64":
            decode_byte_value(value, context)
            return
        for key, child in value.items():
            _validate_byte_values(child, f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_byte_values(child, f"{context}[{index}]")


def _text(value: Any, context: str) -> str:
    return decode_byte_value(value, context).decode("utf-8", errors="replace")


class GetBibleSwordContractReader:
    def __init__(self, schema_path: Path, expected_contract: str) -> None:
        schema = read_json(schema_path)
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)
        self.expected_contract = expected_contract

    def read(self, stream: BinaryIO) -> NativeExport:
        sequence = 0
        stream_hash = hashlib.sha256()
        counts: Counter[str] = Counter()
        header: dict[str, Any] | None = None
        module: dict[str, Any] | None = None
        footer: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        diagnostic_counts: Counter[str] = Counter()
        configuration: dict[str, list[str]] = {}
        artifacts: dict[int, _ArtifactState] = {}
        completed_artifacts: set[int] = set()
        artifact_bytes = 0
        artifact_phase = False
        next_artifact_id = 0
        next_config_source = 0
        next_config_entry = 0

        while raw_line := stream.readline(MAX_RECORD_BYTES + 1):
            if len(raw_line) > MAX_RECORD_BYTES:
                raise ContractError("getbiblesword emitted a record above the 64 MiB limit")
            if not raw_line.endswith(b"\n"):
                raise ContractError("getbiblesword emitted a record without its required LF")
            if raw_line == b"\n":
                raise ContractError("getbiblesword emitted an empty NDJSON record")
            try:
                record = json.loads(raw_line[:-1].decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ContractError(f"Invalid getbiblesword JSON at sequence {sequence}") from error
            if not isinstance(record, dict):
                raise ContractError(f"getbiblesword sequence {sequence} is not an object")
            if footer is not None:
                raise ContractError("getbiblesword emitted records after its footer")
            if list(record) != sorted(record):
                raise ContractError(f"getbiblesword sequence {sequence} is not canonically ordered")
            if record.get("sequence") != sequence:
                raise ContractError(
                    f"Expected getbiblesword sequence {sequence}, "
                    f"received {record.get('sequence')!r}"
                )
            record_type = record.get("type")
            if record_type not in RECORD_TYPES:
                raise ContractError(f"Unknown getbiblesword record type: {record_type!r}")
            errors = sorted(self.validator.iter_errors(record), key=lambda item: list(item.path))
            if errors:
                location = ".".join(str(item) for item in errors[0].path) or "record"
                raise ContractError(
                    f"getbiblesword schema violation at sequence {sequence} ({location}): "
                    f"{errors[0].message}"
                )
            _validate_byte_values(record, f"record[{sequence}]")

            if record_type == "footer":
                footer = record
            else:
                stream_hash.update(raw_line)
                counts[record_type] += 1

            if sequence == 0 and record_type != "header":
                raise ContractError("The first getbiblesword record is not a header")
            if record_type == "header":
                if header is not None or sequence != 0:
                    raise ContractError("getbiblesword emitted more than one header")
                if record.get("contract") != self.expected_contract:
                    raise ContractError(
                        f"Unsupported getbiblesword contract: {record.get('contract')!r}"
                    )
                if record.get("command") != "extract":
                    raise ContractError("Study Builder requires a getbiblesword extract stream")
                header = record
            elif record_type == "module":
                if header is None or module is not None:
                    raise ContractError("getbiblesword emitted an invalid module record sequence")
                module = record
            elif record_type == "config_source":
                if module is None or entries:
                    raise ContractError(
                        "getbiblesword emitted configuration outside its module header"
                    )
                if record.get("ordinal") != next_config_source:
                    raise ContractError("getbiblesword config_source ordinals are not monotonic")
                next_config_source += 1
            elif record_type == "config_entry":
                if module is None or entries:
                    raise ContractError("getbiblesword emitted configuration after logical entries")
                if record.get("ordinal") != next_config_entry:
                    raise ContractError("getbiblesword config_entry ordinals are not monotonic")
                next_config_entry += 1
                name = _text(record["name"], f"record[{sequence}].name").casefold()
                value = _text(record["value"], f"record[{sequence}].value")
                configuration.setdefault(name, []).append(value)
            elif record_type == "entry":
                if module is None or artifact_phase:
                    raise ContractError(
                        "getbiblesword emitted a logical entry in an invalid position"
                    )
                if record.get("ordinal") != len(entries):
                    raise ContractError("getbiblesword entry ordinals are not monotonic")
                entries.append(self._adapt_entry(record, sequence))
            elif record_type == "artifact_begin":
                artifact_phase = True
                artifact_id = record.get("artifact_id")
                if artifact_id != next_artifact_id or artifact_id in completed_artifacts:
                    raise ContractError("getbiblesword artifact identifiers are not monotonic")
                if artifact_id in artifacts:
                    raise ContractError("getbiblesword opened one artifact more than once")
                file_type = record.get("file_type")
                digest = hashlib.sha256()
                state = _ArtifactState(file_type=str(file_type), digest=digest)
                if file_type == "regular":
                    expected_size = record.get("size_expected")
                    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
                        raise ContractError("A regular artifact has no valid size_expected")
                    state.expected_size = expected_size
                elif file_type == "symlink":
                    target = decode_byte_value(record.get("target"), f"record[{sequence}].target")
                    digest.update(target)
                    state.size = len(target)
                elif file_type != "directory":
                    raise ContractError(f"Unknown artifact file type: {file_type!r}")
                artifacts[artifact_id] = state
                next_artifact_id += 1
            elif record_type == "artifact_chunk":
                artifact_id = record.get("artifact_id")
                state = artifacts.get(artifact_id)
                if state is None or state.file_type != "regular":
                    raise ContractError("getbiblesword emitted a chunk for no regular artifact")
                if record.get("index") != state.next_index:
                    raise ContractError("getbiblesword artifact chunk indexes are not monotonic")
                data = decode_byte_value(record.get("data"), f"record[{sequence}].data")
                state.digest.update(data)
                state.size += len(data)
                state.next_index += 1
            elif record_type == "artifact_end":
                artifact_id = record.get("artifact_id")
                state = artifacts.pop(artifact_id, None)
                if state is None:
                    raise ContractError("getbiblesword closed an artifact that was not open")
                size = record.get("size")
                if size != state.size:
                    raise ContractError("getbiblesword artifact size does not match its chunks")
                if state.expected_size is not None and size != state.expected_size:
                    raise ContractError("getbiblesword artifact changed size during extraction")
                digest = record.get("sha256")
                if not isinstance(digest, str) or not hmac.compare_digest(
                    state.digest.hexdigest(), digest.casefold()
                ):
                    raise ContractError("getbiblesword artifact digest does not match its chunks")
                completed_artifacts.add(artifact_id)
                artifact_bytes += state.size
            elif record_type == "diagnostic":
                severity = str(record.get("severity"))
                diagnostic_counts[severity] += 1
                diagnostic = dict(record)
                message = record.get("message")
                if isinstance(message, dict):
                    diagnostic["message_text"] = _text(message, f"record[{sequence}].message")
                diagnostics.append(diagnostic)

            sequence += 1

        if header is None:
            raise ContractError("getbiblesword emitted no header")
        if module is None:
            raise ContractError("getbiblesword emitted no module record")
        if footer is None:
            raise ContractError("getbiblesword stream ended without a footer")
        if artifacts:
            raise ContractError("getbiblesword stream ended with an incomplete artifact")
        self._verify_footer(
            footer,
            stream_hash.hexdigest(),
            counts,
            len(entries),
            len(completed_artifacts),
            artifact_bytes,
            diagnostic_counts,
        )
        metadata = self._adapt_module(module, configuration)
        metadata["_getbiblesword_header"] = header
        metadata["_getbiblesword_record"] = module
        return NativeExport(
            metadata=metadata,
            entries=entries,
            diagnostics=tuple(diagnostics),
            footer=footer,
        )

    @staticmethod
    def _adapt_module(
        record: dict[str, Any], configuration: dict[str, list[str]]
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"classification": record.get("classification", "unknown")}
        for name in ("name", "driver", "description", "language", "sword_type"):
            metadata[name] = _text(record[name], f"module.{name}")
        metadata["configuration"] = {name: tuple(values) for name, values in configuration.items()}
        for name, values in configuration.items():
            if values:
                metadata[name] = values[-1]
        return metadata

    @staticmethod
    def _adapt_entry(record: dict[str, Any], sequence: int) -> dict[str, Any]:
        key = _text(record["key"], f"record[{sequence}].key")
        raw = _text(record["raw"], f"record[{sequence}].raw")
        rendered = record.get("rendered_default")
        stripped = record.get("stripped")
        scope = record.get("scope") or {}
        verse: dict[str, Any] = {}
        if scope.get("type") == "verse_key":
            verse = {
                "testament": scope.get("testament", 0),
                "book": scope.get("book", 0),
                "chapter": scope.get("chapter", 0),
                "verse": scope.get("verse", 0),
                "osis": _text(
                    scope.get("osis_reference"), f"record[{sequence}].scope.osis_reference"
                ),
            }
        return {
            "key": key,
            "raw": raw,
            "html": (
                _text(rendered, f"record[{sequence}].rendered_default")
                if rendered is not None
                else ""
            ),
            "plain": (
                _text(stripped, f"record[{sequence}].stripped") if stripped is not None else ""
            ),
            "verse": verse,
            "_getbiblesword": record,
        }

    @staticmethod
    def _verify_footer(
        footer: dict[str, Any],
        digest: str,
        counts: Counter[str],
        entries: int,
        artifacts: int,
        artifact_bytes: int,
        diagnostic_counts: Counter[str],
    ) -> None:
        expected_hash = footer.get("stream_sha256")
        if not isinstance(expected_hash, str) or not hmac.compare_digest(
            digest, expected_hash.casefold()
        ):
            raise ContractError("getbiblesword footer stream digest does not match")
        if footer.get("counts") != dict(sorted(counts.items())):
            raise ContractError("getbiblesword footer record counts do not match")
        if footer.get("entries") != entries:
            raise ContractError("getbiblesword footer entry count does not match")
        if footer.get("artifacts") != artifacts:
            raise ContractError("getbiblesword footer artifact count does not match")
        if footer.get("artifact_bytes") != artifact_bytes:
            raise ContractError("getbiblesword footer artifact byte count does not match")
        expected_diagnostics = {
            name: diagnostic_counts.get(name, 0) for name in ("error", "info", "warning")
        }
        if footer.get("diagnostics") != expected_diagnostics:
            raise ContractError("getbiblesword footer diagnostic counts do not match")
        if diagnostic_counts.get("error", 0):
            raise ContractError("getbiblesword emitted an error diagnostic")
        if footer.get("success") is not True:
            raise ContractError("getbiblesword reported an unsuccessful extraction")
