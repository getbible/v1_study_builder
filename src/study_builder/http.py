from __future__ import annotations

import hashlib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

USER_AGENT = "GetBible-Study-Builder/1.0 (+https://github.com/getbible/v1_study_builder)"
MAX_MEMORY_RESPONSE = 128 * 1024 * 1024
MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(new_url)
        if old.scheme == "https" and new.scheme != "https":
            raise urllib.error.HTTPError(
                new_url, code, "Refusing to redirect HTTPS to an insecure URL", headers, fp
            )
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            redirected.remove_header("Authorization")
        return redirected


class HttpClient:
    def __init__(self, retries: int = 4, timeout: int = 120) -> None:
        self.retries = retries
        self.timeout = timeout
        self.context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            SafeRedirectHandler(), urllib.request.HTTPSHandler(context=self.context)
        )

    @staticmethod
    def _headers(headers: Mapping[str, str] | None = None) -> dict[str, str]:
        result = {"User-Agent": USER_AGENT}
        result.update(headers or {})
        return result

    def get_bytes(self, url: str, headers: Mapping[str, str] | None = None) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                request = urllib.request.Request(url, headers=self._headers(headers))
                with self.opener.open(request, timeout=self.timeout) as response:
                    content_length = int(response.headers.get("Content-Length", "0") or 0)
                    if content_length > MAX_MEMORY_RESPONSE:
                        raise RuntimeError(f"Response is too large to hold in memory: {url}")
                    payload = response.read(MAX_MEMORY_RESPONSE + 1)
                    if len(payload) > MAX_MEMORY_RESPONSE:
                        raise RuntimeError(f"Response exceeded the memory limit: {url}")
                    return payload
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"Unable to download {url}: {last_error}") from last_error

    def download(
        self,
        url: str,
        target: Path,
        expected_sha256: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".partial")
        last_error: Exception | None = None
        for attempt in range(self.retries):
            digest = hashlib.sha256()
            size = 0
            try:
                request = urllib.request.Request(url, headers=self._headers(headers))
                with (
                    self.opener.open(request, timeout=self.timeout) as response,
                    partial.open("wb") as output,
                ):
                    content_length = int(response.headers.get("Content-Length", "0") or 0)
                    if content_length > MAX_DOWNLOAD:
                        raise RuntimeError(f"Download is larger than the configured limit: {url}")
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > MAX_DOWNLOAD:
                            raise RuntimeError(f"Download exceeded the configured limit: {url}")
                        digest.update(block)
                        output.write(block)
                if expected_sha256 and digest.hexdigest() != expected_sha256.casefold():
                    raise RuntimeError(f"Checksum mismatch for {url}")
                partial.replace(target)
                return target
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
                partial.unlink(missing_ok=True)
                last_error = error
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"Unable to download {url}: {last_error}") from last_error
