import hashlib
import http.client
import io
from unittest.mock import Mock

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


def response(payload: bytes, content_length: int) -> io.BytesIO:
    stream = io.BytesIO(payload)
    stream.headers = {"Content-Length": str(content_length)}
    return stream


@pytest.mark.parametrize("method", ["get_bytes", "download"])
def test_truncated_response_is_retried_before_being_accepted(tmp_path, monkeypatch, method) -> None:
    client = HttpClient(retries=2)
    client.opener = Mock()
    client.opener.open.side_effect = [response(b"part", 8), response(b"complete", 8)]
    monkeypatch.setattr("study_builder.http.time.sleep", lambda seconds: None)
    target = tmp_path / "downloaded.bin"

    if method == "download":
        client.download("https://example.com/data", target)
        payload = target.read_bytes()
    else:
        payload = client.get_bytes("https://example.com/data")

    assert payload == b"complete"
    assert client.opener.open.call_count == 2


@pytest.mark.parametrize("failure", ["content_length", "chunked"])
def test_truncated_download_preserves_cache_and_removes_partial(tmp_path, failure) -> None:
    client = HttpClient(retries=1)
    client.opener = Mock()
    if failure == "content_length":
        incoming = response(b"part", 8)
    else:
        incoming = Mock()
        incoming.__enter__ = Mock(return_value=incoming)
        incoming.__exit__ = Mock(return_value=False)
        incoming.headers = {}
        incoming.read.side_effect = http.client.IncompleteRead(b"part", 8)
    client.opener.open.return_value = incoming
    target = tmp_path / "downloaded.bin"
    target.write_bytes(b"previous complete archive")

    with pytest.raises(RuntimeError, match="Unable to download"):
        client.download("https://example.com/data", target)

    assert target.read_bytes() == b"previous complete archive"
    assert not target.with_suffix(".bin.partial").exists()
