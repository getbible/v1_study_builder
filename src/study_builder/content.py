# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

# The public API publishes plain text only. Markup is never republished, so the
# builder needs no HTML sanitizer and the generated API carries no markup that a
# consuming application could inject into a page.
_SUPPRESSED_TAGS = {"script", "style"}
_BREAK_TAGS = {
    "blockquote",
    "br",
    "dd",
    "div",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "table",
    "tr",
    "ul",
}

_OSIS_REF = re.compile(
    r"(?P<book>[1-4]?[A-Za-z][A-Za-z0-9]+)\.(?P<chapter>\d+)(?:\.(?P<verse>\d+))?"
)
_SWORD_URI = re.compile(r"sword://(?P<value>[^\s\"'<>]+)", re.IGNORECASE)


class _MarkupStripper(HTMLParser):
    """Reduce source markup to readable text without republishing any of it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SUPPRESSED_TAGS:
            self._suppressed += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SUPPRESSED_TAGS:
            self._suppressed = max(0, self._suppressed - 1)
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_markup(value: str) -> str:
    parser = _MarkupStripper()
    parser.feed(value)
    parser.close()
    return parser.text()


def clean_text(value: str, *, unescape: bool = True) -> str:
    if unescape:
        value = html.unescape(value)
    value = value.replace("\x00", "")
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def extract_osis_references(*values: str) -> list[str]:
    references: set[str] = set()
    for value in values:
        for match in _OSIS_REF.finditer(value):
            references.add(match.group(0))
        for uri in _SWORD_URI.finditer(value):
            candidate = uri.group("value")
            for match in _OSIS_REF.finditer(candidate):
                references.add(match.group(0))
    return sorted(references)


def public_content(entry: dict[str, Any]) -> dict[str, str]:
    """Project a validated contract entry onto the published text-only shape."""
    text = clean_text(str(entry.get("plain", "")))
    if not text:
        # A few modules leave the extractor's stripped field empty and carry the
        # definition only in the rendered form. Deriving text keeps those entries
        # addressable instead of dropping them when markup is not republished.
        text = clean_text(strip_markup(str(entry.get("html", ""))), unescape=False)
    return {"text": text}
