import hashlib

import pytest

from study_builder.http import HttpClient


def test_streaming_download_and_checksum(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"study-builder")
    target = tmp_path / "downloaded.bin"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    HttpClient(retries=1).download(source.as_uri(), target, expected_sha256=expected)
    assert target.read_bytes() == b"study-builder"


def test_streaming_download_rejects_bad_checksum(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"study-builder")
    target = tmp_path / "downloaded.bin"
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        HttpClient(retries=1).download(source.as_uri(), target, expected_sha256="0" * 64)
    assert not target.exists()
