# SPDX-License-Identifier: GPL-2.0-only
"""Project a validated source entry onto the published plain text, and read its markup.

The public API publishes plain text only. Markup is never republished, so the builder
needs no HTML sanitizer and the generated API carries no markup that a consuming
application could inject into a page.

ThML, TEI and OSIS source is projected after the extractor contract has verified its
bytes. SWORD's stripped projections lose structure and, for some formats, character
references. Source-aware projection keeps readable blocks, labels, notes and references
without adding markup to the public contract. Other formats retain SWORD's stripped
projection. Entity decoding happens only while reading markup, never on plain text.

Whatever the source, the published text is normalised the same way: one space between
words, no leading or trailing space on a line, at most one blank line between blocks.
"""

from __future__ import annotations

import html
import re
from functools import partial
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from study_builder.osis import osis_text
from study_builder.references import MarkupReference
from study_builder.xmltext import decode_xml_entities, xml_attributes, xml_character_reference

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
# TEI dictionaries share HTML's paragraph tags but use their own list/table names.
# In TEI, ``tr`` is a translation, not an HTML table row.
_TEI_BLOCK_TAGS = _BLOCK_TAGS | {"entry", "entryfree", "head", "lg", "list", "sense"}
_TEI_LINE_TAGS = (_LINE_TAGS - {"tr"}) | {"item", "l", "lb", "row"}
_TEI_CELL_TAGS = _CELL_TAGS | {"cell"}

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
    r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?(?:reference|ref|scripRef))\b"
    r"(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</(?P=tag)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR = re.compile(
    r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?a)\b"
    r"(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</(?P=tag)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_ATTRIBUTE = re.compile(
    r"""(?P<name>[A-Za-z][A-Za-z0-9:_-]*)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')"""
)


class _MarkupStripper(HTMLParser):
    """Reduce rendered markup to readable text without republishing any of it."""

    def __init__(self, *, convert_charrefs: bool = True) -> None:
        super().__init__(convert_charrefs=convert_charrefs)
        self._parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in _SUPPRESSED_TAGS:
            self._suppressed += 1
        elif tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self._parts.append("\n")
        elif tag in _CELL_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in _SUPPRESSED_TAGS:
            self._suppressed = max(0, self._suppressed - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self._parts.append(data)

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            self.handle_data(data[len("CDATA[") :])

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


class _TeiToText(_MarkupStripper):
    """Read TEI's visible structure while retaining all unsuppressed text.

    The entry/sense labels and line, paragraph, table and list boundaries follow
    SWORD's TEI rendering conventions. Notes are kept in brackets because a text API
    has no separate popup in which to display their contents. Unknown elements keep
    their text; no dictionary-specific entries or language-specific rules are used.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._notes: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in _SUPPRESSED_TAGS:
            self._suppressed += 1
            return
        if self._suppressed:
            return
        if tag in _TEI_BLOCK_TAGS or tag in _TEI_LINE_TAGS:
            self._parts.append("\n")
        elif tag in _TEI_CELL_TAGS:
            self._parts.append(" ")
        elif tag == "note":
            self._notes.append(len(self._parts))
            self._parts.append(" [")
        if tag in {"entry", "entryfree", "sense", "item"}:
            label = xml_attributes(self.get_starttag_text() or "").get("n")
            if label:
                self._parts.append(label + " ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in _SUPPRESSED_TAGS:
            self._suppressed = max(0, self._suppressed - 1)
        elif self._suppressed:
            return
        elif tag in _TEI_BLOCK_TAGS or tag in {"item", "l", "row"}:
            self._parts.append("\n")
        elif tag in _TEI_CELL_TAGS:
            self._parts.append(" ")
        elif tag == "note" and self._notes:
            start = self._notes.pop()
            if "".join(self._parts[start + 1 :]).strip():
                self._parts.append("] ")
            else:
                del self._parts[start:]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Process empty XML elements as a pair. In particular, an empty note or
        # script must never suppress the rest of the entry.
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            # XML formatting whitespace is not a displayed line break.
            self._parts.append(data.replace("\r", " ").replace("\n", " "))

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            # CDATA is already literal text, including any entity-looking words.
            self.handle_data(data[len("CDATA[") :])

    def handle_entityref(self, name: str) -> None:
        # Accept complete named entities, retaining unknown names literally.
        # HTML's permissive partial decoding would turn ``&notit;`` into ``¬it;``.
        if not self._suppressed:
            self._parts.append(decode_xml_entities(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if not self._suppressed:
            self._parts.append(xml_character_reference(name))


class _ReferenceSource(_MarkupStripper):
    """Keep active reference markup separate from literal examples and hidden text."""

    def __init__(self, source_type: str) -> None:
        super().__init__(convert_charrefs=False)
        self._osis = source_type.strip().casefold() == "osis"
        self._hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        local = tag.rsplit(":", 1)[-1]
        if self._hidden:
            if local in _SUPPRESSED_TAGS or local == "note":
                self._hidden.append(local)
            return
        values = xml_attributes(self.get_starttag_text() or "")
        if local in _SUPPRESSED_TAGS or (
            self._osis
            and local == "note"
            and values.get("type", "").casefold() in {"strongsmarkup", "x-strongsmarkup"}
        ):
            self._hidden.append(local)
        else:
            self._parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if self._hidden:
            if self._hidden[-1] == tag.rsplit(":", 1)[-1]:
                self._hidden.pop()
        else:
            self._parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._hidden and tag.rsplit(":", 1)[-1] not in _SUPPRESSED_TAGS:
            self._parts.append(self.get_starttag_text() or "")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.handle_data(" ")

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            # The visible example still contributes to reference occurrence counts,
            # but literal <ref> characters must not be mistaken for an active tag.
            self.handle_data(html.escape(data[len("CDATA[") :], quote=False))


def _reference_source(value: str, source_type: str) -> str:
    parser = _ReferenceSource(source_type)
    parser.feed(value)
    parser.close()
    return parser.text()


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


def tei_text(value: str) -> str:
    """The plain text of validated TEI source, including its visible structure."""
    parser = _TeiToText()
    parser.feed(value)
    parser.close()
    return parser.text()


def normalize_text(value: str) -> str:
    """One space between words, trimmed lines, at most one blank line between blocks."""
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def clean_text(value: str) -> str:
    """Normalize already-plain text without interpreting it as escaped markup."""
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


def _attributes(value: str, source_type: str = "") -> dict[str, str]:
    if source_type.strip().casefold() in {"tei", "osis"}:
        return xml_attributes(value)
    return {
        match.group("name").casefold(): html.unescape(match.group("dq") or match.group("sq") or "")
        for match in _ATTRIBUTE.finditer(value)
    }


def _display(inner: str | None, source_type: str = "") -> str:
    """The words a reference shows, projected as the text is so they can be found in it."""
    projector = {"tei": tei_text, "osis": osis_text}.get(source_type.strip().casefold(), thml_text)
    return " ".join(projector(inner or "").split())


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


def extract_markup_references(*values: str, source_type: str = "") -> list[MarkupReference]:
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
        value = _reference_source(value, source_type)
        located: list[tuple[int, int, str, str, str]] = []
        for match in _REFERENCE_TAG.finditer(value):
            attrs = _attributes(match.group("attrs"), source_type)
            display = _display(match.group("inner"), source_type)
            tag = match.group("tag").rsplit(":", 1)[-1].casefold()
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
            attrs = _attributes(match.group("attrs"), source_type)
            href = attrs.get("href", "")
            display = _display(match.group("inner"), source_type)
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
            before += _display(value[cursor:start], source_type) + " "
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


class ContentProjectionError(ValueError):
    """A source entry contains text but its public projection would discard it."""


def public_content(entry: dict[str, Any], *, source_type: str = "") -> dict[str, str]:
    """Project a validated contract entry onto the unchanged v1 text-only shape."""
    if error := entry.get("_text_error"):
        raise ContentProjectionError(
            f"Entry {entry.get('key', 'unknown')!r} "
            f"({source_type or 'unspecified SourceType'}): {error}"
        )
    projector = {"thml": thml_text, "tei": tei_text, "osis": osis_text}.get(
        source_type.strip().casefold()
    )
    if source_type.strip().casefold() == "osis":
        projector = partial(osis_text, testament=entry.get("verse", {}).get("testament"))
    raw = str(entry.get("raw", ""))
    rendered = str(entry.get("html", ""))
    if projector and raw:
        text = clean_text(projector(raw))
    else:
        text = clean_text(str(entry.get("plain", "")))
    if not text:
        # rendered_default may still be source markup, not HTML: use the same
        # format-aware reader so a fallback cannot reintroduce flattened TEI/OSIS.
        text = clean_text((projector or strip_markup)(rendered))
    if not text and clean_text(strip_markup(_reference_source(raw, source_type))):
        key = entry.get("key", entry.get("osis_id", "unknown"))
        raise ContentProjectionError(
            f"Entry {key!r} ({source_type or 'unspecified SourceType'}) contains source text "
            "but its public text projection is empty; inspect the validated raw, stripped "
            "and rendered fields before publishing this module"
        )
    return {"text": text}
