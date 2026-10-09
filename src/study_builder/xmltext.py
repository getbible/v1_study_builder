# SPDX-License-Identifier: GPL-2.0-only
"""Decode XML presentation text without HTML's lossy entity substitutions."""

from __future__ import annotations

import re
from html.entities import html5

_ENTITY = re.compile(r"&(?P<name>\#[xX][0-9A-Fa-f]+|\#[0-9]+|[A-Za-z][A-Za-z0-9]*);")
_ATTRIBUTE = re.compile(
    r"""(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')"""
)


def xml_character_reference(name: str) -> str:
    """Decode a numeric reference, preserving invalid XML characters literally."""
    try:
        codepoint = int(name[1:], 16) if name[:1].casefold() == "x" else int(name, 10)
        if (
            (codepoint < 0x20 and codepoint not in {9, 10, 13})
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in {0xFFFE, 0xFFFF}
        ):
            raise ValueError("Not a valid XML character")
        return chr(codepoint)
    except (ValueError, OverflowError):
        return f"&#{name};"


def decode_xml_entities(value: str) -> str:
    """Decode complete references exactly once, never a prefix of an unknown name.

    Legacy TEI/OSIS sources also use named entities such as ``&nbsp;`` and
    ``&Alpha;``. Accept those exact names without HTML's permissive partial-name
    matching or its Windows-1252 remapping of numeric references.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name.startswith("#"):
            return xml_character_reference(name[1:])
        return html5.get(name + ";", match.group(0))

    return _ENTITY.sub(replace, value)


def xml_attributes(tag_source: str) -> dict[str, str]:
    """Read quoted XML attributes directly from the original start tag.

    HTMLParser always decodes attribute values using HTML rules, including when
    ``convert_charrefs=False``. Reading its original tag text avoids that earlier,
    irreversible conversion. Names are case-folded just as in HTMLParser's API.
    """
    return {
        match.group("name").casefold(): decode_xml_entities(
            match.group("dq") if match.group("dq") is not None else match.group("sq")
        )
        for match in _ATTRIBUTE.finditer(tag_source)
    }
