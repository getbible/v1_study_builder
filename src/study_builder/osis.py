# SPDX-License-Identifier: GPL-2.0-only
"""Readable plain text from validated OSIS markup, without flattening its structure.

This is a presentation adapter, not a SWORD module reader. It reads only the Unicode
source that the extractor contract has already verified. The word annotations and
bracketed notes follow SWORD's OSISPlain conventions; paragraph, poetry, list and
milestone boundaries follow its display filters. Unknown inline elements retain
their text, so an unrecognised presentation tag cannot discard a definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from study_builder.xmltext import decode_xml_entities, xml_attributes, xml_character_reference

_BLOCKS = {"chapter", "div", "lg", "list", "p", "table", "title"}
_LINES = {"item", "l", "row", "verse"}
_FRAMED = {"divinename", "hi", "note", "q", "w"}
_SUPPRESSED = {"script", "style"}
_OVERLINE = {"ol", "overline", "x-overline"}


@dataclass
class _Frame:
    tag: str
    attrs: dict[str, str]
    start: int


def _local(tag: str) -> str:
    return tag.rsplit(":", 1)[-1].casefold()


def _unprefixed(value: str) -> str:
    return value.partition(":")[2] if ":" in value else value


class _OsisToText(HTMLParser):
    def __init__(self, testament: int | None) -> None:
        # OSIS is XML: numeric references name Unicode codepoints. HTML's automatic
        # decoding remaps C1 references through Windows-1252, changing source text.
        super().__init__(convert_charrefs=False)
        self._testament = testament
        self._parts: list[str] = []
        self._frames: list[_Frame] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(_local(tag), xml_attributes(self.get_starttag_text() or ""), False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(_local(tag), xml_attributes(self.get_starttag_text() or ""), True)

    def _start(self, tag: str, attrs: dict[str, str], empty: bool) -> None:
        if tag in _SUPPRESSED:
            if not empty:
                self._suppressed += 1
            return
        if self._suppressed:
            return
        if tag in _BLOCKS:
            self._parts.append("\n")
        elif tag in _LINES:
            self._line_break()
        elif tag in {"lb", "br"}:
            # Optional breaks are layout hints, not a break in the source prose.
            if attrs.get("type") != "x-optional":
                self._parts.append("\n")
        elif tag == "cell":
            self._parts.append(" ")
        elif tag == "milestone":
            kind = attrs.get("type", "").casefold()
            if kind in {"line", "x-p", "paragraph"}:
                self._parts.append("\n")
            if "marker" in attrs:
                self._parts.append(attrs["marker"])
        elif tag == "q" and empty:
            if "sid" in attrs or "eid" in attrs:
                self._parts.append(attrs.get("marker", ""))
        elif tag == "w" and empty:
            self._parts.append(self._word_annotations(attrs))
        elif tag in _FRAMED and not empty:
            self._frames.append(_Frame(tag, attrs, len(self._parts)))

    def handle_endtag(self, tag: str) -> None:
        tag = _local(tag)
        if tag in _SUPPRESSED:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if self._suppressed:
            return
        if tag in _BLOCKS:
            self._parts.append("\n")
        elif tag in _LINES:
            self._line_break()
        elif tag == "cell":
            self._parts.append(" ")
        elif tag in _FRAMED:
            for index in range(len(self._frames) - 1, -1, -1):
                if self._frames[index].tag == tag:
                    # Entry fragments need not have all surrounding container tags.
                    # Finish any nested frame rather than dropping its text.
                    while len(self._frames) > index:
                        self._finish(self._frames.pop())
                    break

    def _line_break(self) -> None:
        # A poetry line or list item ends on the same boundary on which the next
        # begins. XML indentation between tags must not create a blank line.
        for part in reversed(self._parts):
            if part.strip(" \t"):
                if not part.rstrip(" \t").endswith("\n"):
                    self._parts.append("\n")
                return

    def _finish(self, frame: _Frame) -> None:
        text = "".join(self._parts[frame.start :])
        del self._parts[frame.start :]
        if frame.tag == "w":
            text += self._word_annotations(frame.attrs)
        elif frame.tag == "note":
            if frame.attrs.get("type", "").casefold() in {"strongsmarkup", "x-strongsmarkup"}:
                # SWORD reserves these notes for annotation bookkeeping; they are
                # not visible footnotes. Ordinary explanatory notes remain intact.
                text = ""
            elif text.strip():
                text = f" [{text.strip()}] "
        elif frame.tag == "divinename":
            text = text.upper()
        elif frame.tag == "hi" and text.strip():
            kind = frame.attrs.get("type") or frame.attrs.get("rend", "")
            if kind.casefold() in _OVERLINE:
                text = "".join(character + "\u0305" for character in text)
            else:
                text = f"* {text} *"
        elif frame.tag == "q":
            # Explicit markers are source content. Do not invent default quotation
            # marks: the OSISqToTick display setting is not part of this projection.
            marker = frame.attrs.get("marker", "")
            text = marker + text + marker
        self._parts.append(text)

    def _word_annotations(self, attrs: dict[str, str]) -> str:
        parts: list[str] = []
        if attrs.get("xlit"):
            parts.append(f" <{_unprefixed(attrs['xlit'])}>")
        if attrs.get("gloss"):
            parts.append(f" <{attrs['gloss']}>")
        for lemma in attrs.get("lemma", "").split():
            value = _unprefixed(lemma)
            if value.isascii() and value.isdigit() and self._testament in {1, 2}:
                value = ("H" if self._testament == 1 else "G") + value
            if value:
                parts.append(f" <{value}>")
        for morph in attrs.get("morph", "").split():
            value = _unprefixed(morph)
            if len(value) > 2 and value[:2] in {"TG", "TH"} and value[2].isdigit():
                value = value[2:]
            if value:
                parts.append(f" ({value})")
        if attrs.get("pos"):
            parts.append(f" <{_unprefixed(attrs['pos'])}>")
        return "".join(parts)

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            # Newlines used to lay out the XML itself are ordinary whitespace.
            self._parts.append(data.replace("\r", " ").replace("\n", " "))

    def handle_entityref(self, name: str) -> None:
        if not self._suppressed:
            self._parts.append(decode_xml_entities(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if not self._suppressed:
            self._parts.append(xml_character_reference(name))

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            self.handle_data(data[6:])

    def text(self) -> str:
        while self._frames:
            self._finish(self._frames.pop())
        return "".join(self._parts)


def osis_text(value: str, *, testament: int | None = None) -> str:
    """Project OSIS source into unnormalised plain text, decoding entities once.

    ``testament`` is the extractor's verified verse context (1 = OT, 2 = NT), used
    only for Strong's lemma numbers that omit their G/H prefix. Without context,
    retain the source number rather than guessing its language.
    """
    parser = _OsisToText(testament)
    parser.feed(value)
    parser.close()
    return parser.text()
