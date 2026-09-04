# SPDX-License-Identifier: GPL-2.0-only
"""Project a validated source entry onto the published plain text, and read its markup.

The public API publishes plain text only. Markup is never republished, so the builder
needs no HTML sanitizer and the generated API carries no markup that a consuming
application could inject into a page.

The text comes from the extractor's ``stripped`` projection — SWORD's own plain-text
reading of the module — with one exception. SWORD's ThML plain filter collapses every
line and paragraph break into a space, so a ThML dictionary or commentary would publish
each entry as one unbroken paragraph however its source was laid out. For ThML modules
the builder therefore reads the source markup itself, keeping SWORD's conventions
(entities, Strong's and morphology markers, bracketed notes) and keeping the breaks.

Whatever the source, the published text is normalised the same way: one space between
words, no leading or trailing space on a line, at most one blank line between blocks.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from study_builder.references import MarkupReference

_SUPPRESSED_TAGS = {"script", "style"}
# A block stands apart from what surrounds it: a break before and after.
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "center",
    "div",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}
# A line starts on its own line; the next one follows directly beneath it.
_LINE_TAGS = {"br", "dd", "dt", "li", "tr"}
# Cells of a table row stay on one line, separated by a space.
_CELL_TAGS = {"td", "th"}

_OSIS_REF = re.compile(
    r"(?P<book>[1-4]?[A-Za-z][A-Za-z0-9]+)\.(?P<chapter>\d+)(?:\.(?P<verse>\d+))?"
)
_SWORD_URI = re.compile(r"sword://(?P<value>[^\s\"'<>]+)", re.IGNORECASE)
# Every horizontal white space character, whatever its script: \s is Unicode-aware.
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_NOTE_SPACE = re.compile(r" \[\s*\]\s?")

# The markup that carries a scripture reference, in each family SWORD reads:
# OSIS <reference osisRef>, TEI <ref osisRef|target>, ThML <scripRef passage|parsed>,
# and the sword://Bible/ link any of them may use. The rendered form spells them all
# one way, as passagestudy.jsp showRef anchors, which is read as well.
_REFERENCE_TAG = re.compile(
    r"<(?P<tag>reference|ref|scripRef)\b(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</(?P=tag)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR = re.compile(
    r"<a\b(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</a\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ATTRIBUTE = re.compile(
    r"""(?P<name>[A-Za-z][A-Za-z0-9:_-]*)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')"""
)


class _MarkupStripper(HTMLParser):
    """Reduce rendered markup to readable text without republishing any of it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SUPPRESSED_TAGS:
            self._suppressed += 1
        elif tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self._parts.append("\n")
        elif tag in _CELL_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SUPPRESSED_TAGS:
            self._suppressed = max(0, self._suppressed - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


class _ThmlToText(_MarkupStripper):
    """SWORD's ThML plain-text conventions, with the line structure kept.

    ThMLPlain writes a Strong's ``sync`` as ``<G3056>``, a morphology ``sync`` as
    ``(V-PAI-3S)`` and a ``note`` as ``[…]``, and then collapses every break. This
    projection keeps those conventions, so the words published for a ThML module do not
    change, and keeps ``<br>``, ``<p>`` and the other block tags as line breaks.
    """

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "sync":
            self._sync(attrs)
        elif tag == "note":
            self._parts.append(" [")
        else:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "note":
            self._parts.append("] ")
        else:
            super().handle_endtag(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "sync":
            self._sync(attrs)
        else:
            super().handle_startendtag(tag, attrs)

    def handle_data(self, data: str) -> None:
        # A line break in ThML source is white space, as it is in HTML.
        if not self._suppressed:
            self._parts.append(data.replace("\r", " ").replace("\n", " "))

    def _sync(self, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value or "" for name, value in attrs}
        kind = values.get("type", "").casefold()
        value = values.get("value", "").strip()
        if not value:
            return
        if kind == "strongs":
            self._parts.append(f" <{value}>")
        elif kind == "morph":
            self._parts.append(f" ({value})")


def strip_markup(value: str) -> str:
    parser = _MarkupStripper()
    parser.feed(value)
    parser.close()
    return parser.text()


def thml_text(value: str) -> str:
    """The plain text of ThML source, breaks included."""
    parser = _ThmlToText()
    parser.feed(value)
    parser.close()
    return _NOTE_SPACE.sub(" ", parser.text())


def normalize_text(value: str) -> str:
    """One space between words, trimmed lines, at most one blank line between blocks."""
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def clean_text(value: str, *, unescape: bool = True) -> str:
    if unescape:
        value = html.unescape(value)
    return normalize_text(value)


def extract_osis_references(*values: str) -> list[str]:
    """Every OSIS-shaped identifier in the given markup, sorted; kept for callers that want ids."""
    references: set[str] = set()
    for value in values:
        for match in _OSIS_REF.finditer(value):
            references.add(match.group(0))
        for uri in _SWORD_URI.finditer(value):
            candidate = uri.group("value")
            for match in _OSIS_REF.finditer(candidate):
                references.add(match.group(0))
    return sorted(references)


def _attributes(value: str) -> dict[str, str]:
    return {
        match.group("name").casefold(): html.unescape(match.group("dq") or match.group("sq") or "")
        for match in _ATTRIBUTE.finditer(value)
    }


def _display(inner: str | None) -> str:
    """The words a reference shows, projected as the text is so they can be found in it."""
    return " ".join(thml_text(inner or "").split())


def _bible_target(value: str) -> str | None:
    """The reference inside a sword:// link or a work-prefixed target, if it is scripture."""
    value = unquote(value.strip())
    lowered = value.casefold()
    if lowered.startswith("sword://"):
        rest = value[len("sword://") :]
        work, _slash, reference = rest.partition("/")
        if work.casefold() != "bible":
            return None
        return reference.strip() or None
    if ":" in value and not value.split(":", 1)[0].strip().isdigit():
        work, reference = value.split(":", 1)
        if work.strip().casefold() != "bible":
            return None
        return reference.strip() or None
    return value or None


def _is_osis(reference: str) -> bool:
    """Whether every token of a parsed reference is an OSIS identifier; CCEL's ThML also
    writes "|Gen|4|2|4|2", which is not."""
    tokens = reference.split()
    return bool(tokens) and all(_OSIS_REF.fullmatch(token.split("-")[0]) for token in tokens)


def extract_markup_references(*values: str) -> list[MarkupReference]:
    """Every scripture reference the markup carries, in document order, with its display text.

    The source markup is read first and the rendered form only when the source carries
    no reference at all: both describe the same tags, so reading both would publish
    each citation twice. A ``scripRef`` is read from its ``passage`` — the citation as
    SWORD renders it — and from ``parsed`` only where there is no passage and the value
    really is OSIS. Each reference records how often its display text occurs in the
    text before it, so that a display without a book name, "1:1", is located at its own
    citation and not inside an earlier one.
    """
    found: list[MarkupReference] = []
    seen: set[tuple[str, str, str]] = set()

    def kind_of(reference: str) -> str:
        head = reference.split()[0].split("-")[0]
        return "osis" if _OSIS_REF.fullmatch(head) else "passage"

    for value in values:
        # Comments carry no live markup, and every kind of tag is read in the order the
        # document has them, so their display texts are located in the text in turn.
        value = _COMMENT.sub(" ", value)
        located: list[tuple[int, int, str, str, str]] = []
        for match in _REFERENCE_TAG.finditer(value):
            attrs = _attributes(match.group("attrs"))
            display = _display(match.group("inner"))
            tag = match.group("tag").casefold()
            span = (match.start(), match.end())
            if tag == "scripref":
                parsed = attrs.get("parsed", "").replace("|", " ").strip()
                if attrs.get("passage", "").strip():
                    located.append((*span, "passage", attrs["passage"], display))
                elif parsed and _is_osis(parsed):
                    located.append((*span, "osis", parsed, display))
                elif display:
                    located.append((*span, "passage", display, display))
                continue
            osis = attrs.get("osisref", "").strip()
            if osis:
                located.append((*span, "osis", osis, display))
                continue
            target = _bible_target(attrs.get("target", ""))
            if target:
                located.append((*span, kind_of(target), target, display))
        for match in _ANCHOR.finditer(value):
            attrs = _attributes(match.group("attrs"))
            href = attrs.get("href", "")
            display = _display(match.group("inner"))
            lowered = href.casefold()
            span = (match.start(), match.end())
            if lowered.startswith("sword://"):
                target = _bible_target(href)
                if target:
                    located.append((*span, kind_of(target), target, display))
                continue
            if "action=showref" not in lowered:
                continue
            query = parse_qs(urlsplit(href.replace("&amp;", "&")).query)
            reference = " ".join(query.get("value", [""])).strip()
            if reference:
                located.append((*span, kind_of(reference), reference, display))
        before = ""
        cursor = 0
        for start, end, kind, reference, display in sorted(located, key=lambda item: item[0]):
            before += " ".join(strip_markup(value[cursor:start]).split()) + " "
            cursor = end
            occurrence = before.count(display) if display else 0
            before += display + " "
            reference = " ".join(reference.split())
            key = (kind, reference.casefold(), display.casefold())
            if not reference or key in seen:
                continue
            seen.add(key)
            found.append(MarkupReference(kind, reference, display, occurrence))
        if found:
            break
    return found


def public_content(entry: dict[str, Any], *, source_type: str = "") -> dict[str, str]:
    """Project a validated contract entry onto the published text-only shape."""
    if source_type.strip().casefold() == "thml":
        text = clean_text(thml_text(str(entry.get("raw", ""))), unescape=False)
    else:
        text = clean_text(str(entry.get("plain", "")))
    if not text:
        # A few modules leave the extractor's stripped field empty and carry the
        # definition only in the rendered form. Deriving text keeps those entries
        # addressable instead of dropping them when markup is not republished.
        text = clean_text(strip_markup(str(entry.get("html", ""))), unescape=False)
    return {"text": text}
