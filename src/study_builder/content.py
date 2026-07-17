from __future__ import annotations

import html
import re
from typing import Any

import bleach

ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "*": ["class", "dir", "lang", "title"],
}
ALLOWED_PROTOCOLS = {"http", "https", "mailto", "sword"}

_OSIS_REF = re.compile(
    r"(?P<book>[1-4]?[A-Za-z][A-Za-z0-9]+)\.(?P<chapter>\d+)(?:\.(?P<verse>\d+))?"
)
_SWORD_URI = re.compile(r"sword://(?P<value>[^\s\"'<>]+)", re.IGNORECASE)


def clean_html(value: str) -> str:
    return bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    ).strip()


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\x00", "")
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


def public_content(entry: dict[str, Any]) -> dict[str, Any]:
    text = clean_text(str(entry.get("plain", "")))
    rendered = clean_html(str(entry.get("html", "")))
    result: dict[str, Any] = {"text": text}
    visible_rendered = clean_text(bleach.clean(rendered, tags=set(), strip=True))
    if rendered and visible_rendered and rendered != text:
        result["html"] = rendered
    return result
