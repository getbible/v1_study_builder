# SPDX-License-Identifier: GPL-2.0-only
"""Recognise scripture references and resolve them against the Bible API.

A module cites scripture in two ways. Some mark every citation up — an OSIS
``osisRef``, a ThML ``scripRef`` passage, a ``sword://`` link — and those are read
from the markup. Many carry citations only as prose: ``son of Adam, slain by Cain
Ge 4:2,8; Mt 23:35; Heb 11:4; 12:24``. Both kinds pass through one resolver, so a
published reference means the same thing whatever its source.

Recognising a book name is the multilingual part, and it is the getBible
librarian's job: its per-translation alias tables and Unicode-normalising trie
(the ``getbible`` package, the engine behind query.getbible.net) resolve ``1Sa``,
``Första Moseboken`` or ``Ma-thi-ơ`` to a book number. ``conf/book_aliases`` adds
the spellings the current modules use that those tables lack, in the same format,
so they can be contributed upstream. Whether a chapter or verse exists is not a
question for this repository at all: it is answered by the Bible API's own
documents through :mod:`study_builder.bible`. Every published coordinate therefore
resolves on the API, and every ``ref`` is a reference the librarian — and so the
Query API — accepts.

The prose conventions are the ones the source modules use:

- a citation starts with a book name, in any spelling the tables list for the
  module's language, followed by a chapter and usually a verse;
- ``,`` continues a list of verses, ``-`` spans a range, and ``;`` starts the
  next chapter of the same book, so ``Heb 11:4; 12:24`` cites Hebrews twice;
- a chapter and verse with no book at all — ``12:24``, or ``In 9:46 he is
  called`` — belongs to the book named most recently, in text order;
- ``ver. 7`` belongs to the chapter cited most recently.

The entry text is never rewritten. Each recognised citation is published with the
exact text it was recognised from, so a client can find it again, and with ``ref``
restating it canonically.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from getbible import GetBibleReference, ReferenceValidationError
from getbible.getbible_reference_trie import GetBibleReferenceTrie

from study_builder.bible import BibleApi, Canon
from study_builder.books import BookRegistry

_NUM = r"\d{1,3}"
# U+FFFD stands where a byte could not be decoded; in the modules seen so far that
# byte was the non-breaking space of "1 Chronicles".
_SP_CHARS = " \t\u00a0\u2009\u202f\ufffd"
_SP = f"[{_SP_CHARS}]"
_DASH = "[-\u2010\u2011\u2012\u2013\u2014\u2212]"
# Letters of the Latin script, including the accented forms Swedish and Vietnamese use.
_LETTER = "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff"
# A book name may follow a lowercase word directly — a Vietnamese gloss runs straight
# into "Lu 3:38" — but not an uppercase one, which is a heading such as "REVERENCEGe".
_UPPER = "A-Z\u00c0-\u00d6\u00d8-\u00de"
_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
}
# Five, not four: Swedish counts the books of Moses, so "5 Mos" is Deuteronomy. A digit
# may run straight into the name ("1Sa"); a roman numeral or a word needs a separator,
# or the "I" of "Isa" would be one.
_ORDINAL = (
    rf"(?:[1-5](?:st|nd|rd|th)?|(?:IV|III|II|I|V|First|Second|Third|Fourth|Fifth)"
    rf"(?=[{_SP_CHARS}.]))"
)
_ORDINAL_PREFIX = re.compile(rf"^({_ORDINAL})[{_SP_CHARS}.]*(.+)$", re.IGNORECASE)

# A number that ends here: the next character is not another digit, and not the
# comma of a thousands separator — "2,322 men" is a count, not a chapter and a verse
# list. A list may still run "Ps 119:97,100-102": only a three-digit group beyond
# 176, the last verse of that psalm, or one with a leading zero, is a separator.
_THOUSANDS_GROUP = r"(?:0\d\d|[2-9]\d\d|1[89]\d|17[7-9])"
_THOUSANDS = rf"(?!,{_THOUSANDS_GROUP})"
# "Ps 88 (89):40" cites the psalm under two numberings, the Septuagint's first and
# the Hebrew — which is the API's — in brackets; the bracketed chapter is the one
# published.
_HEAD = re.compile(
    rf"(?P<chapter>{_NUM})(?:{_SP}*\((?P<chapter_alt>{_NUM})\)(?={_SP}*:))?"
    rf"(?:(?:{_SP}*:{_SP}*|\.)(?P<verse>{_NUM}))?(?!\d){_THOUSANDS}"
)
# Webster writes chapters in roman numerals: "Rom. i. 28." A roman chapter is read
# only directly after a book name, and only with a verse; "i. e." is not Genesis 1.
_ROMAN_HEAD = re.compile(rf"(?P<roman>[ivxlc]{{1,7}})\.{_SP}*(?P<verse>{_NUM})(?!\d)")
_ROMAN = re.compile(r"^(?:c{0,1})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_RANGE = (
    rf"(?:{_SP}*{_DASH}{_SP}*(?:(?P<end_chapter>{_NUM})[:.])?(?P<end_verse>{_NUM})(?!\d){_THOUSANDS}"
    rf"|{_SP}+to{_SP}+(?P<to_chapter>{_NUM}):(?P<to_verse>{_NUM})(?!\d))?"
)
# "8:1 ff" and "12:5 f." cite the verse and those that follow it; the suffix is kept
# in the text but adds no verse, since where the passage ends is not stated.
_SUFFIX = rf"(?:{_SP}?ff?(?![{_LETTER}])\.?)?"
_VERSE_TAIL = re.compile(_RANGE + _SUFFIX)
_VERSE_MORE = re.compile(
    rf"{_SP}*,{_SP}*(?:(?P<chapter>{_NUM})[:.])?(?P<verse>{_NUM})(?!\d){_THOUSANDS}"
    + _RANGE
    + _SUFFIX
)
_LOWER = "a-z\u00df-\u00f6\u00f8-\u00ff"
# A bare chapter is only continued when what follows cannot be prose: "Ex 28; 29;
# Le 8" continues, "Ps 99:6; 4 sons of" does not, and nor does "Ex 28; 29, and".
_DELIMITED = (
    rf"(?={_SP}*(?:,(?!{_THOUSANDS_GROUP})(?!{_SP}*[{_LOWER}])|[^{_LETTER}\d{_SP_CHARS},]|$))"
)
_CHAPTER_TAIL = re.compile(rf"(?:{_SP}*{_DASH}{_SP}*(?P<end_chapter>{_NUM})(?!\d)(?![:.]\d))?")
_CHAPTER_MORE = re.compile(
    rf"{_SP}*,{_SP}*(?P<chapter>{_NUM})(?:[:.](?P<verse>{_NUM})(?!\d)|(?!\d)(?![:.]\d){_DELIMITED})"
)
_NEXT = re.compile(
    rf"{_SP}*;{_SP}*(?P<chapter>{_NUM})(?:[:.](?P<verse>{_NUM})(?!\d)|(?!\d)(?![:.]\d){_DELIMITED})"
)
# A bare chapter in a table row — "the Exodus 1 | | 430" — is a tally, not a chapter.
_TABLE_CELL = re.compile(rf"{_SP}*\|")
# A chapter and verse standing alone in prose. A time of day is not a citation, nor
# is the fraction of "7:1/4 acres", but "38:31 Am 5:8" cites Amos: a marker followed
# by a chapter is a book, not a meridiem.
_PROSE = re.compile(
    rf"(?<![{_LETTER}\d:.,/-])(?P<chapter>{_NUM}):(?P<verse>{_NUM})(?!\d)(?!/\d)"
    rf"(?!{_SP}*(?:(?i:[ap]\.?{_SP}*m\.?)(?![{_LETTER}])(?!{_SP}*[(\[]?{_SP}*\d)"
    rf"|(?i:o['’]clock)))"
)
# "ver. 7" cites a verse of the chapter last named; "ver. 1:22" names its own chapter
# and is read as prose, and the "V." of "A.V. 2:14" is not the word verse.
_VERSE_ONLY = re.compile(
    rf"(?<![{_LETTER}.])(?:(?:vv|ver|verses|verse)\.?{_SP}?|v\.{_SP})(?P<verse>{_NUM})"
    rf"(?!\d)(?![:.]\d)"
    rf"(?:{_SP}*{_DASH}{_SP}*(?P<end_verse>{_NUM})(?!\d))?"
    rf"(?P<more>(?:{_SP}*,{_SP}*{_NUM}(?!\d)(?:{_SP}*{_DASH}{_SP}*{_NUM}(?!\d))?)*)",
    re.IGNORECASE,
)
_VERSE_ITEM = re.compile(rf"({_NUM})(?:{_SP}*{_DASH}{_SP}*({_NUM}))?")
_SENSE = re.compile(rf"\.{_SP}+\d")
# A bare chapter followed by a comma and a lowercase word is prose, not a chapter list.
_PROSE_CONTINUES = re.compile(rf"{_SP}*,{_SP}*[a-z\u00df-\u00f6\u00f8-\u00ff]")
# The one month whose abbreviation is also a book: "Mar. 8" is a date.
_MONTHS = frozenset({"mar"})
_LETTER_AT = re.compile(rf"[{_LETTER}]")
_PRECEDING_WORD = re.compile(rf"([{_LETTER}][{_LETTER}'’.\-]*)\.?$")
# Abbreviated titles of other works: what follows "Ep. Jer", "Test. Levi" or "Apoc.
# Bar" is not the biblical book of that name. Only the abbreviation, with its full
# stop, is a title; "a test Joh 11:3" cites John.
_TITLE_PREFIXES = frozenset({"ep", "epist", "test", "apoc", "asc"})
_CLOSERS = "'’\"”»)]"
_SENTENCE_END = ".;:!?\n—–("
_OSIS_ID = re.compile(
    r"^(?:[A-Za-z0-9]+:)?(?P<book>[1-5]?[A-Za-z][A-Za-z0-9]*)"
    r"(?:\.(?P<chapter>\d{1,3})(?:\.(?P<verse>\d{1,3}))?)?(?:!.*)?$"
)
_OSIS_TAIL = re.compile(r"^(?:(?P<chapter>\d{1,3})\.)?(?P<verse>\d{1,3})(?:!.*)?$")

# Words that introduce a citation of scripture, whatever their case: "See 4:2",
# "Comp. 1:9", "ver. 7".
_MARKERS = frozenset(
    {
        "see",
        "comp",
        "cp",
        "cf",
        "cfr",
        "confer",
        "compare",
        "comparing",
        "read",
        "reads",
        "cited",
        "quoted",
        "quoting",
        "note",
        "notes",
        "esp",
        "especially",
        "viz",
        "eg",
        "e.g",
        "ie",
        "i.e",
        "etc",
        "ff",
        "sq",
        "sqq",
        "seq",
        "chap",
        "chaps",
        "ch",
        "chs",
        "chapter",
        "chapters",
        "verse",
        "verses",
        "ver",
        "vv",
        "v",
        "vs",
        "also",
        "again",
        "vid",
        "vide",
        "ib",
        "ibid",
        "jfr",
        "se",
        "xem",
        "sánh",
        # Sigla of the text a citation is taken from: "(LXX 4:8)", "A.V. 2:14".
        "lxx",
        "a.v",
        "av",
        "r.v",
        "rv",
        "asv",
        "rsv",
        "erv",
        "kjv",
        "rec",
        "wh",
        "tr",
        "th",
        "aq",
        "sm",
        "al",
        "syr",
        "vulg",
        "vg",
        "targ",
        "heb",
        "gr",
        "sept",
        "mss",
        "ms",
        "cod",
        "codd",
    }
)
# Ordinary words: a citation may follow them, but only where they open a sentence.
# Mid-sentence, a capitalised one belongs to the title of another work — "Sib Or
# 3:271" — and so does any other capitalised word: "Ant. 11:8", "Enoch 6:6".
_CONNECTIVES = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "nor",
        "for",
        "so",
        "as",
        "yet",
        "of",
        "in",
        "at",
        "on",
        "to",
        "by",
        "from",
        "with",
        "into",
        "unto",
        "under",
        "over",
        "after",
        "before",
        "through",
        "against",
        "about",
        "between",
        "among",
        "within",
        "without",
        "during",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "that",
        "this",
        "these",
        "those",
        "there",
        "here",
        "where",
        "when",
        "which",
        "who",
        "whom",
        "whose",
        "what",
        "than",
        "then",
        "thus",
        "hence",
        "only",
        "not",
        "no",
        "its",
        "it",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "och",
        "samt",
        "även",
        "jämför",
        "và",
        "trong",
    }
)
# Nouns after which two numbers around a colon are a ratio, a time, or a page, not
# a chapter and a verse.
_NOT_A_CITATION = frozenset(
    {
        "ratio",
        "ratios",
        "proportion",
        "proportions",
        "odds",
        "vol",
        "vols",
        "volume",
        "volumes",
        "page",
        "pages",
        "pp",
        "pg",
        "no",
        "nos",
        "num",
        "hours",
        "hour",
        "minutes",
        "sura",
        "surah",
        "col",
        "cols",
        "column",
        "line",
        "lines",
        "sect",
        "sec",
        "act",
        "scene",
        "canto",
        "bk",
        "lib",
        "tom",
    }
)

WHOLE_CHAPTER = object()
_UNSEARCHED = object()


@dataclass(frozen=True)
class MarkupReference:
    """A citation the source markup carries: what it points at, and how the text spells it."""

    kind: str  # "osis" for an osisRef value, "passage" for a human citation
    value: str
    display: str = ""
    # How often the display text occurs in the text before this citation.
    occurrence: int = 0


@dataclass(frozen=True)
class Span:
    """One passage inside a citation, as written: a chapter, a verse, or a range."""

    chapter: int
    verse: int | None = None
    end_chapter: int | None = None
    end_verse: int | None = None


@dataclass(frozen=True)
class Guard:
    """A stretch of text a markup reference already covers; prose scanning skips it."""

    start: int
    end: int
    book: int | None
    chapter: int | None


@dataclass
class _Context:
    """What an unqualified citation inherits: the book, and the chapter, last named."""

    book: int | None = None
    chapter: int | None = None


def split_ordinal(name: str) -> tuple[int | None, str]:
    """Separate "1 Sam", "I Sam", "1st Sam" or "First Sam" into an ordinal and a bare name."""
    match = _ORDINAL_PREFIX.match(name.strip())
    if not match:
        return None, name.strip()
    return _ordinal_value(match.group(1)), match.group(2).strip()


def _ordinal_value(value: str) -> int:
    folded = value.casefold()
    if folded[:1].isdigit():
        return int(folded[0])
    return _ORDINAL_WORDS[folded]


def _names_pattern(names: Iterable[str]) -> str:
    """One alternation for every spelling, grouped by first letter.

    The first letter must be written as listed; the rest may vary in case, so
    "GENESIS 1:1" is recognised but a lowercase "is 6:1" is left alone, and later
    words vary freely: the Swedish table lists "Höga v" and the module writes "Höga
    V.". Grouping by first letter lets the regex engine reject a position after one
    character instead of trying several hundred spellings there; on a long
    encyclopaedia article that is the difference between seconds and minutes.
    """
    groups: dict[str, list[str]] = {}
    for name in sorted(set(names), key=lambda value: (-len(value), value)):
        words = name.split(" ")
        rest = re.escape(words[0][1:]) + "".join(f"{_SP}+{re.escape(w)}" for w in words[1:])
        groups.setdefault(name[0], []).append(rest)
    parts = []
    for first, rests in sorted(groups.items()):
        parts.append(re.escape(first) + f"(?i:{'|'.join(rests)})")
    return "|".join(parts)


def _letters(value: str) -> int:
    return sum(1 for character in value if character.isalpha())


def _compress(numbers: list[int]) -> str:
    """Write a sorted verse list the way a reference does: "2,8", "9-14", "9-10,12"."""
    parts: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


class BookAliases:
    """How a language spells the books, for recognising names in prose and resolving them.

    The spellings come, in order of precedence, from
    ``conf/book_aliases/modules/{module}.json`` for the conventions one module alone
    follows — Abbott-Smith names the books as the Septuagint does, so its "I Ki" is
    1 Samuel — from ``conf/book_aliases/{language}.json``, from the librarian's alias
    tables for the translations the module is checked against, and from the names
    those translations publish in ``books.json``. Every table is loaded into the
    librarian's own trie, so a name resolves exactly as it would in a Query API
    request.
    """

    def __init__(
        self, canon: Canon, aliases_dir: Path | None = None, module_id: str | None = None
    ) -> None:
        self.canon = canon
        self._tries: list[GetBibleReferenceTrie] = []
        self._supplement: dict[int, list[str]] = {}
        self._librarian: dict[int, list[str]] = {}
        self.librarian_translations: list[str] = []
        self.supplements: list[Path] = []
        if aliases_dir is not None:
            candidates = [aliases_dir / f"{canon.language}.json"]
            if module_id:
                candidates.insert(0, aliases_dir / "modules" / f"{module_id}.json")
            for supplement in candidates:
                if supplement.is_file():
                    self.supplements.append(supplement)
                    self._load(supplement, self._supplement)
        translations = [canon.names_translation, *canon.translations]
        for translation in dict.fromkeys(t for t in translations if t):
            resource = files("getbible").joinpath("data", f"{translation}.json")
            if resource.is_file():
                with as_file(resource) as path:
                    self._load(path, self._librarian)
                self.librarian_translations.append(translation)
        self._exact: dict[str, int] = {}
        for number, name in canon.names.items():
            self._exact.setdefault(_normalize(name), number)
        self.pattern = self._compile()

    def _load(self, path: Path, into: dict[int, list[str]]) -> None:
        trie = GetBibleReferenceTrie()
        trie.load(str(path))
        self._tries.append(trie)
        with path.open(encoding="utf-8") as handle:
            table = json.load(handle)
        for number, names in table.items():
            into.setdefault(int(number), []).extend(str(name) for name in names)

    @property
    def enabled(self) -> bool:
        return self.pattern is not None

    def spellings(self, book: int) -> list[str]:
        """Every spelling listed for a book: the librarian's own first, then the supplement."""
        return list(
            dict.fromkeys([*self._librarian.get(book, ()), *self._supplement.get(book, ())])
        )

    def _every_spelling(self) -> Iterable[str]:
        for table in (self._supplement, self._librarian):
            for names in table.values():
                yield from names
        yield from self.canon.names.values()

    def resolve(self, name: str) -> int | None:
        """The book number a spelling names, exactly as the librarian would resolve it."""
        ordinal, bare = split_ordinal(name)
        query = f"{ordinal} {bare}" if ordinal else bare
        for trie in self._tries:
            found = trie.search(query)
            if found and found.isdigit():
                return int(found)
        return self._exact.get(_normalize(query))

    def _compile(self) -> re.Pattern[str] | None:
        bare: dict[str, str] = {}
        for name in self._every_spelling():
            _ordinal, rest = split_ordinal(name.rstrip("."))
            # One letter names a book only in a query; in prose "G 3:16" is noise.
            if _letters(rest) < 2 or rest[:1].isdigit():
                continue
            bare.setdefault(rest.casefold(), rest)
        if not bare:
            return None
        return re.compile(
            rf"(?<![{_UPPER}])(?:(?P<ordinal>{_ORDINAL}){_SP}*\.?{_SP}*\n?{_SP}*)?"
            rf"(?P<name>{_names_pattern(bare.values())})"
            rf"(?:\.{_SP}*|,{_SP}+(?=\d{{1,3}}{_SP}*:)|{_SP}+|(?={_SP}*\n)|(?=\d{{1,3}}:\d))"
            rf"(?=\n?{_SP}*[(\[]?{_SP}*(?:\d|[ivxlc]{{1,7}}\.{_SP}*\d))"
        )

    def at(self, text: str, position: int) -> int | None:
        """The book whose name starts exactly at ``position``, or None."""
        if self.pattern is None:
            return None
        match = self.pattern.match(text, position)
        if match is None:
            return None
        found = self._resolve_match(text, match)
        return found[2] if found and found[0] == position else None

    def find(self, text: str, position: int) -> tuple[int, re.Match[str], int] | None:
        """Find the next book name at or after ``position``: its start, match, and number.

        A number before the name is read as its ordinal when the two together name a
        book that has the chapter cited, even where a Vietnamese Strong's number runs
        straight into it — "G16402 Cô 8:15" is 2 Corinthians, not Colossians. Where
        they do not, the name stands alone: the "3" of "Heb 1:1-3 Joh 17:2" ends a
        range, since 3 John has no chapter 17; and a digit wedged between a number
        and the name with at most a full stop between them — the "4" of "H894Ma
        1:11" or "H1162.Ma 1:5" — is the number's last digit.
        """
        if self.pattern is None:
            return None
        while True:
            match = self.pattern.search(text, position)
            if not match:
                return None
            found = self._resolve_match(text, match)
            if found is not None:
                return found
            position = match.start() + 1

    def _resolve_match(
        self, text: str, match: re.Match[str]
    ) -> tuple[int, re.Match[str], int] | None:
        ordinal = match.group("ordinal")
        name = match.group("name")
        start = match.start()
        readings: list[tuple[int, int]] = []
        if ordinal:
            wedged = (
                start > 0
                and text[start - 1].isdigit()
                and text[match.end("ordinal") : match.start("name")] in ("", ".")
            )
            if not wedged:
                book = self.resolve(f"{_ordinal_value(ordinal)} {name}")
                if book is not None:
                    readings.append((start, book))
        book = self.resolve(name)
        if book is not None:
            readings.append((match.start("name"), book))
        if not readings:
            return None
        chapter = _CHAPTER_AFTER.match(text, match.end())
        if chapter is not None:
            for start, book in readings:
                if self.canon.has_chapter(book, int(chapter.group("chapter"))):
                    return start, match, book
        start, book = readings[0]
        return start, match, book


_CHAPTER_AFTER = re.compile(rf"\n?{_SP}*[(\[]?{_SP}*(?P<chapter>{_NUM})(?!\d)")


def _normalize(name: str) -> str:
    import unicodedata

    value = unicodedata.normalize("NFC", name).replace(".", "")
    value = re.sub(r"(\d)\s+(\w)", r"\1\2", value)
    return " ".join(value.split()).casefold()


class ReferenceResolver:
    """Turn the passages of a citation into Bible API coordinates the API publishes."""

    def __init__(
        self,
        canon: Canon,
        aliases: BookAliases,
        registry: BookRegistry,
        checker: GetBibleReference | None = None,
    ) -> None:
        self.canon = canon
        self.aliases = aliases
        self.registry = registry
        # The librarian's default limits harden a public request; here it validates what
        # the builder writes, and a topical entry can list forty verses of one psalm.
        self.checker = (
            checker
            if checker is not None
            else GetBibleReference(cache_limit=5000, max_reference_length=4096)
        )
        self.translation = canon.names_translation or canon.translations[0]
        self._names: dict[int, str] = {}

    def name(self, book: int) -> str:
        """The book name ``ref`` uses: the API's name where the librarian accepts it.

        The librarian is what parses a Query API request, so a name it does not know
        is no use to a client; then the first spelling its own table lists is used,
        and failing even that the book number, which the Query API also accepts.
        """
        if book in self._names:
            return self._names[book]
        candidates = [self.canon.name(book) or "", *self.aliases.spellings(book)]
        chosen = str(book)
        for candidate in candidates:
            if not candidate or candidate[:1].isdigit() and " " not in candidate:
                continue
            try:
                resolved = self.checker.ref(f"{candidate} 1", self.translation)
            except ReferenceValidationError:
                continue
            if resolved.book == book:
                chosen = candidate
                break
        self._names[book] = chosen
        return chosen

    def items(self, book: int, spans: Iterable[Span], cited: str | None = None) -> list[dict]:
        """Publish one citation of ``book`` as one item per chapter it covers.

        A chapter the book does not have, and a verse a chapter does not have, are not
        published: the API has nothing at that address. A range that crosses a chapter
        boundary is expanded with the API's verse counts, so "Nu 16:1-17:13" names every
        verse it covers, once per chapter.
        """
        if not self.canon.has_book(book) or book not in self.registry.by_number:
            return []
        chapters: dict[int, set[int] | object] = {}

        def whole(chapter: int) -> None:
            chapters[chapter] = WHOLE_CHAPTER

        def verses(chapter: int, numbers: Iterable[int]) -> None:
            current = chapters.get(chapter)
            if current is WHOLE_CHAPTER:
                return
            if current is None:
                current = chapters[chapter] = set()
            assert isinstance(current, set)
            current.update(numbers)

        count = self.canon.chapter_count(book) or 0
        for span in spans:
            if not self.canon.has_chapter(book, span.chapter):
                continue
            if span.verse is None:
                last = min(span.end_chapter or span.chapter, count)
                for chapter in range(span.chapter, max(last, span.chapter) + 1):
                    whole(chapter)
                continue
            if span.verse < 1:
                continue
            limit = self.canon.verse_count(book, span.chapter) or 0
            if span.end_chapter is None or span.end_chapter == span.chapter:
                if span.verse > limit:
                    continue
                last = span.end_verse if span.end_verse and span.end_verse >= span.verse else None
                verses(span.chapter, range(span.verse, min(last or span.verse, limit) + 1))
                continue
            if span.end_chapter < span.chapter or span.end_chapter > count:
                if span.verse <= limit:
                    verses(span.chapter, [span.verse])
                continue
            if span.verse <= limit:
                verses(span.chapter, range(span.verse, limit + 1))
            for chapter in range(span.chapter + 1, span.end_chapter):
                whole(chapter)
            end_limit = self.canon.verse_count(book, span.end_chapter) or 0
            last = min(max(span.end_verse or 1, 1), end_limit)
            verses(span.end_chapter, range(1, last + 1))

        osis = self.registry.by_number[book].osis[0]
        name = self.name(book)
        items: list[dict[str, Any]] = []
        for chapter, covered in chapters.items():
            item: dict[str, Any] = {}
            if cited is not None:
                item["text"] = cited
            if covered is WHOLE_CHAPTER:
                item["ref"] = f"{name} {chapter}"
                item.update({"osis": f"{osis}.{chapter}", "book": book, "chapter": chapter})
                items.append(item)
                continue
            assert isinstance(covered, set)
            if not covered:
                continue
            numbers = sorted(covered)
            spec = _compress(numbers)
            item["ref"] = f"{name} {chapter}:{spec}"
            item.update(
                {
                    "osis": f"{osis}.{chapter}.{numbers[0]}",
                    "book": book,
                    "chapter": chapter,
                    "verse": numbers[0],
                }
            )
            if len(numbers) > 1:
                item["verses"] = numbers
            self._check(book, chapter, spec, numbers)
            items.append(item)
        return items

    def _check(self, book: int, chapter: int, spec: str, numbers: list[int]) -> None:
        """The librarian must read the published verses back exactly as they were written."""
        parsed = self.checker.ref(f"{book} {chapter}:{spec}", self.translation)
        if (parsed.book, parsed.chapter, sorted(parsed.verses)) != (book, chapter, numbers):
            raise RuntimeError(
                f"The librarian reads {book} {chapter}:{spec} differently from the builder"
            )

    def from_osis(self, value: str) -> list[dict]:
        """Resolve one or more OSIS references, ranges included: "Num.21.6-Num.21.9"."""
        items: list[dict] = []
        for token in value.split():
            start, _dash, end = token.partition("-")
            head = _OSIS_ID.match(start)
            if not head or head.group("chapter") is None:
                continue
            book = self.registry.by_osis.get(head.group("book").casefold())
            if book is None:
                continue
            chapter = int(head.group("chapter"))
            verse = int(head.group("verse")) if head.group("verse") else None
            span = Span(chapter, verse)
            if end:
                tail = _OSIS_ID.match(end)
                if tail and tail.group("chapter") is not None:
                    if self.registry.by_osis.get(tail.group("book").casefold()) is book:
                        end_chapter = int(tail.group("chapter"))
                        end_verse = int(tail.group("verse")) if tail.group("verse") else None
                        if verse is None and end_verse is None:
                            span = Span(chapter, None, end_chapter, None)
                        elif verse is not None and end_verse is not None:
                            span = Span(chapter, verse, end_chapter, end_verse)
                else:
                    short = _OSIS_TAIL.match(end)
                    if short and verse is not None:
                        end_chapter = (
                            int(short.group("chapter")) if short.group("chapter") else None
                        )
                        span = Span(chapter, verse, end_chapter, int(short.group("verse")))
                    elif short and verse is None and short.group("chapter") is None:
                        span = Span(chapter, None, int(short.group("verse")), None)
            items.extend(self.items(book.number, [span]))
        return items


class ProseScanner:
    """Recognise the scripture references written inside plain text."""

    def __init__(self, aliases: BookAliases, resolver: ReferenceResolver) -> None:
        self.aliases = aliases
        self.resolver = resolver

    @property
    def enabled(self) -> bool:
        return self.aliases.enabled

    def scan(
        self,
        text: str,
        *,
        book: int | None = None,
        chapter: int | None = None,
        guards: Sequence[Guard] = (),
        strict: bool = False,
    ) -> list[tuple[int, dict]]:
        """Every reference recognised in ``text`` with the position it was found at.

        ``book`` and ``chapter`` seed what an unqualified citation inherits before the
        text names any book itself: a commentary on John may say ``3:16`` and mean its
        own book. ``guards`` are stretches a markup reference already covers; they are
        skipped, and the book they cite is inherited by what follows. ``strict`` reads a
        string known to be a citation, so nothing is refused for looking like prose.
        """
        if not self.enabled:
            return []
        context = _Context(book if book and self.aliases.canon.has_book(book) else None)
        context.chapter = chapter if context.book and chapter else None
        return _Scan(self, text, context, sorted(guards, key=lambda g: g.start), strict).run()


class _Scan:
    def __init__(
        self,
        scanner: ProseScanner,
        text: str,
        context: _Context,
        guards: list[Guard],
        strict: bool,
    ) -> None:
        self.aliases = scanner.aliases
        self.resolver = scanner.resolver
        self.text = text
        self.context = context
        self.guards = guards
        self.strict = strict
        self.found: list[tuple[int, dict]] = []

    def run(self) -> list[tuple[int, dict]]:
        position = 0
        guard_index = 0
        length = len(self.text)
        # The next candidate of each kind is searched for once and kept until the scan
        # passes it; searching all three afresh at every step reads a long entry
        # once per citation, which the largest encyclopaedia articles cannot afford.
        cached: dict[str, tuple[int, Any] | None] = {}
        while position < length:
            candidates: list[tuple[int, str, Any]] = []
            for kind in ("book", "prose", "verse"):
                found = cached.get(kind, _UNSEARCHED)
                if found is _UNSEARCHED or (found is not None and found[0] < position):
                    # None is remembered too: a kind with no match left is not
                    # searched for again, or every step would read to the end.
                    found = self._next(kind, position)
                    cached[kind] = found
                if found is not None:
                    candidates.append((found[0], kind, found[1]))
            if not candidates:
                break
            start, kind, match = min(candidates, key=lambda item: item[0])
            inside = None
            while guard_index < len(self.guards) and self.guards[guard_index].start <= start:
                guard = self.guards[guard_index]
                guard_index += 1
                if guard.book is not None:
                    self.context = _Context(guard.book, guard.chapter)
                if start < guard.end:
                    inside = guard
            if inside is not None:
                position = max(inside.end, position + 1)
                continue
            if kind == "book":
                position = self._book_citation(*match)
            elif kind == "prose":
                position = self._prose_citation(match)
            else:
                position = self._verse_citation(match)
        return self.found

    def _next(self, kind: str, position: int) -> tuple[int, Any] | None:
        """The next candidate of one kind at or after ``position``: its start and match."""
        if kind == "book":
            found = self.aliases.find(self.text, position)
            return (found[0], found) if found else None
        pattern = _PROSE if kind == "prose" else _VERSE_ONLY
        match = pattern.search(self.text, position)
        return (match.start(), match) if match else None

    # -- citation forms -----------------------------------------------------

    def _book_citation(self, start: int, match: re.Match[str], book: int) -> int:
        position = self._skip_spaces(match.end())
        wrapped = False
        if self.text[position] == "\n":
            # Strong's Hebrew wraps its lines mid-citation: "Judges\n 5:14". Only a
            # chapter and verse may follow a line break; a bare number on the next
            # line is far more often a numbered sense than a chapter.
            wrapped = True
            position = self._skip_spaces(position + 1)
        explicit = True
        if self.text[position] in "([":
            # "Mark (5:1)": the name stands outside the brackets, so the citation
            # inside them is published as written and the name is restored in ref.
            position = self._skip_spaces(position + 1)
            explicit = False
        if self.aliases.at(self.text, position) is not None:
            # "Ezra (2 Esdras 1:2)": the number is the ordinal of the next book, not
            # a chapter of this one, so this name is not a citation at all.
            return match.end()
        if self._title_before(start):
            # "Ep. Jer 5" cites the Epistle of Jeremy, not Jeremiah.
            return match.end()
        name = match.group("name")
        head = _HEAD.match(self.text, position)
        if head is None:
            roman = _ROMAN_HEAD.match(self.text, position)
            if roman is None or _roman_value(roman.group("roman")) is None:
                return match.end()
            chapter = _roman_value(roman.group("roman"))
            assert chapter is not None
            head_values = (chapter, int(roman.group("verse")), roman.end())
        else:
            head_values = _head_values(head)
        if head_values[1] is None and not self.strict:
            assert head is not None
            if (
                wrapped
                or (not explicit and not _NEXT.match(self.text, head.end()))
                or _SENSE.match(self.text, head.end())
                or _LETTER_AT.match(self.text, head.end())
                or _PROSE_CONTINUES.match(self.text, head.end())
                or _TABLE_CELL.match(self.text, head.end())
                or (len(name) > 1 and name.isupper())
                or name.casefold() in _MONTHS
            ):
                # "Prov. 2. 2. Fig.:" numbers the senses of a general dictionary, "John
                # 3rd" is not John 3, "PHILIP (1)" is a numbered headword, "See
                # HEBREWS 2." a cross-reference to a topic, "Mar. 8, and" a date and
                # "Ex 3, which" a chapter running on into prose. None cites a chapter.
                if not explicit and not name.isupper():
                    # "Daniel (8)" still names the book a later "9:26" belongs to.
                    self.context = _Context(book, None)
                return match.end()
        end, spans = self._spans(*head_values, book=book)
        self.context = _Context(book, _last_chapter(spans))
        self._emit(start if explicit else position, end, spans)
        return self._continuations(end, emit=True)

    def _prose_citation(self, match: re.Match[str]) -> int:
        head = _HEAD.match(self.text, match.start())
        assert head is not None
        if self.context.book is None or not self._plain_before(match.start()):
            # Nothing to inherit, or the citation of another work: skip it and
            # whatever continues it, so neither "Enoch 6:6; 8:1" nor "Enoch 6:6, 8:1"
            # leaks its tail.
            end, _spans = self._spans(*_head_values(head), book=None)
            return self._continuations(end, emit=False)
        end, spans = self._spans(*_head_values(head), book=self.context.book)
        self.context.chapter = _last_chapter(spans) or self.context.chapter
        self._emit(match.start(), end, spans)
        return self._continuations(end, emit=True)

    def _verse_citation(self, match: re.Match[str]) -> int:
        context = self.context
        if context.book is None or context.chapter is None:
            return match.end()
        if not self._plain_before(match.start()):
            return match.end()
        spans = [Span(context.chapter, int(match.group("verse")), None, _end(match, "end_verse"))]
        for verse, end_verse in _VERSE_ITEM.findall(match.group("more") or ""):
            spans.append(Span(context.chapter, int(verse), None, int(end_verse or 0) or None))
        self._emit(match.start(), match.end(), spans)
        return match.end()

    def _guarded(self, position: int) -> bool:
        """Whether ``position`` lies inside a stretch a markup reference covers."""
        return any(guard.start <= position < guard.end for guard in self.guards)

    def _continuations(self, position: int, *, emit: bool) -> int:
        """Consume "; 12:24" and "; 29" chunks that continue the current book."""
        while True:
            match = _NEXT.match(self.text, position)
            if match is None or self._guarded(match.start("chapter")):
                # What the markup covers is the markup's to say, not the list's.
                return position
            head = _HEAD.match(self.text, match.start("chapter"))
            assert head is not None
            end, spans = self._spans(*_head_values(head), book=self.context.book if emit else None)
            if emit:
                self.context.chapter = _last_chapter(spans) or self.context.chapter
                self._emit(match.start("chapter"), end, spans)
            position = end

    # -- pieces ---------------------------------------------------------------

    def _skip_spaces(self, position: int) -> int:
        while position < len(self.text) and self.text[position] in _SP_CHARS:
            position += 1
        return position

    def _plain_before(self, position: int) -> bool:
        """Whether what precedes a bookless citation lets it be scripture at all."""
        index = position
        while index > 0 and self.text[index - 1] in _SP_CHARS + _CLOSERS + ",;":
            index -= 1
        # Only the word ending here matters; an unanchored search from the start of a
        # long article would read all of it for every bookless citation.
        match = _PRECEDING_WORD.search(self.text, max(0, index - 64), index)
        if match is None:
            return True
        word = match.group(1).strip(".'’-")
        folded = word.casefold()
        if folded in _NOT_A_CITATION:
            return False
        if not word[:1].isupper() or folded in _MARKERS:
            return True
        if folded in _CONNECTIVES:
            # A passage attribute is a citation whatever word opens it; in prose the
            # word must open the sentence, or it belongs to another work's title.
            return self.strict or self._opens_sentence(match.start())
        return False

    def _title_before(self, position: int) -> bool:
        """Whether the word before a book name makes it the title of another work."""
        index = position
        while index > 0 and self.text[index - 1] in _SP_CHARS:
            index -= 1
        if index == 0 or self.text[index - 1] != ".":
            return False
        match = _PRECEDING_WORD.search(self.text, max(0, index - 16), index)
        return match is not None and match.group(1).strip(".").casefold() in _TITLE_PREFIXES

    def _opens_sentence(self, position: int) -> bool:
        index = position
        while index > 0 and self.text[index - 1] in _SP_CHARS + _CLOSERS:
            index -= 1
        return index == 0 or self.text[index - 1] in _SENTENCE_END

    def _book_at(self, position: int) -> bool:
        """Whether a book name — "2 Sam" — starts exactly at ``position``."""
        return self.aliases.at(self.text, position) is not None

    def _range_opens_book(self, match: re.Match[str]) -> bool:
        """Whether the number after a range dash is the ordinal of the next book."""
        for group in ("end_chapter", "end_verse", "to_chapter"):
            if match.group(group):
                return self._book_at(match.start(group))
        return False

    def _spans(
        self, chapter: int, verse: int | None, end: int, *, book: int | None
    ) -> tuple[int, list[Span]]:
        """Read one citation chunk from its first number, which ends at ``end``, to where
        the numbers stop.

        A bare number after a book of one chapter is a verse: "Jude 7" and "Ob 3" cite
        verses, as every reader takes them to, and the API has no chapter 7 of Jude.
        """
        if (
            verse is None
            and chapter != 1
            and book is not None
            and self.aliases.canon.chapter_count(book) == 1
        ):
            chapter, verse = 1, chapter
        spans: list[Span] = []
        if verse is not None:
            tail = _VERSE_TAIL.match(self.text, end)
            assert tail is not None
            if self._range_opens_book(tail):
                # "2Sa 2:4-1Ki 2:11": the dash joins two citations; it is not a range.
                spans.append(Span(chapter, verse))
            else:
                spans.append(_range_span(chapter, verse, tail))
                end = tail.end()
                # "1Ki 15:1-16:7,12": what follows a range across chapters continues
                # the chapter it ends in.
                chapter = spans[-1].end_chapter or chapter
        else:
            tail = _CHAPTER_TAIL.match(self.text, end)
            assert tail is not None
            spans.append(Span(chapter, None, _end(tail, "end_chapter"), None))
            end = tail.end()
        while True:
            if verse is not None:
                more = _VERSE_MORE.match(self.text, end)
                if more is None:
                    break
                next_start = more.start("chapter") if more.group("chapter") else more.start("verse")
                if self._book_at(next_start) or self._guarded(next_start):
                    # "Ps 1:1, 2 Sam 3:4": the number opens the next book, not this list;
                    # and a number the markup covers is the markup's to read.
                    break
                chapter = _end(more, "chapter") or chapter
                if self._range_opens_book(more):
                    spans.append(Span(chapter, int(more.group("verse"))))
                    end = more.end("verse")
                    break
                spans.append(_range_span(chapter, int(more.group("verse")), more))
                chapter = spans[-1].end_chapter or chapter
            else:
                more = _CHAPTER_MORE.match(self.text, end)
                if (
                    more is None
                    or self._book_at(more.start("chapter"))
                    or self._guarded(more.start("chapter"))
                ):
                    break
                chapter = int(more.group("chapter"))
                verse = _end(more, "verse")
                if verse is not None:
                    tail = _VERSE_TAIL.match(self.text, more.end())
                    assert tail is not None
                    spans.append(_range_span(chapter, verse, tail))
                    end = tail.end()
                    continue
                spans.append(Span(chapter))
            end = more.end()
        return end, spans

    def _emit(self, start: int, end: int, spans: list[Span]) -> None:
        book = self.context.book
        if book is None:
            return
        cited = self.text[start:end]
        for item in self.resolver.items(book, spans, cited):
            self.found.append((start, item))


def _end(match: re.Match[str] | None, group: str) -> int | None:
    if match is None:
        return None
    value = match.group(group)
    return int(value) if value else None


def _head_values(head: re.Match[str]) -> tuple[int, int | None, int]:
    """The chapter, verse and end of a citation head; a bracketed alternative chapter
    numbering — the Hebrew one after the Septuagint's — is the API's."""
    chapter = _end(head, "chapter_alt") or int(head.group("chapter"))
    return chapter, _end(head, "verse"), head.end()


def _last_chapter(spans: list[Span]) -> int | None:
    """The chapter a citation leaves the reader in, for what follows to inherit."""
    if not spans:
        return None
    return spans[-1].end_chapter or spans[-1].chapter


def _roman_value(numeral: str) -> int | None:
    """The value of a lowercase roman numeral, or None where the letters are not one."""
    if not _ROMAN.match(numeral):
        return None
    total = 0
    for index, letter in enumerate(numeral):
        value = _ROMAN_VALUES[letter]
        following = _ROMAN_VALUES[numeral[index + 1]] if index + 1 < len(numeral) else 0
        total += -value if value < following else value
    return total or None


def _range_span(chapter: int, verse: int, tail: re.Match[str]) -> Span:
    end_chapter = _end(tail, "end_chapter") or _end(tail, "to_chapter")
    end_verse = _end(tail, "end_verse") or _end(tail, "to_verse")
    return Span(chapter, verse, end_chapter, end_verse)


def _dedupe_key(item: dict) -> tuple:
    return (item["book"], item["chapter"], item.get("verse"), tuple(item.get("verses", ())))


class ReferenceEngine:
    """Everything one module needs to publish its scripture references."""

    def __init__(
        self,
        canon: Canon,
        registry: BookRegistry,
        aliases_dir: Path | None = None,
        checker: GetBibleReference | None = None,
        module_id: str | None = None,
    ) -> None:
        self.canon = canon
        self.registry = registry
        self.aliases = BookAliases(canon, aliases_dir, module_id)
        self.resolver = ReferenceResolver(canon, self.aliases, registry, checker)
        self.scanner = ProseScanner(self.aliases, self.resolver)

    @classmethod
    def for_module(
        cls,
        bible: BibleApi,
        registry: BookRegistry,
        language: str,
        versification: str,
        aliases_dir: Path | None = None,
        checker: GetBibleReference | None = None,
        module_id: str | None = None,
    ) -> ReferenceEngine:
        canon = bible.canon_for(language, versification)
        return cls(canon, registry, aliases_dir, checker, module_id)

    def describe(self) -> dict[str, Any]:
        """What a module's metadata records about how its references were resolved."""
        return {
            **self.canon.describe(),
            "librarian": list(self.aliases.librarian_translations),
            "aliases": [
                f"modules/{path.name}" if path.parent.name == "modules" else path.name
                for path in self.aliases.supplements
            ],
        }

    def from_osis(self, value: str) -> list[dict]:
        return self.resolver.from_osis(value)

    def from_passage(
        self, passage: str, *, book: int | None = None, chapter: int | None = None
    ) -> list[dict]:
        """Resolve a citation known to be one, such as a ThML scripRef passage."""
        found = self.scanner.scan(passage, book=book, chapter=chapter, strict=True)
        items = []
        for _position, item in found:
            item.pop("text", None)
            items.append(item)
        return _dedupe(items)

    def extract(
        self,
        text: str,
        *,
        markup: Sequence[MarkupReference] = (),
        book: int | None = None,
        chapter: int | None = None,
    ) -> list[dict]:
        """Every reference of one entry, in text order, from its markup and its prose.

        A markup reference is authoritative for the stretch of text it covers: its
        display text is located in the entry text, published as ``text`` beside the
        markup's own coordinates, and not scanned again. What the markup does not cover
        is read as prose, inheriting the book the markup last cited.
        """
        found: list[tuple[int, int, dict]] = []
        guards: list[Guard] = []
        cursor = 0
        order = 0
        context = _Context(book, chapter)
        for reference in markup:
            if reference.kind == "osis":
                items = self.resolver.from_osis(reference.value)
            else:
                items = self.from_passage(
                    reference.value, book=context.book, chapter=context.chapter
                )
            display = " ".join(reference.display.split())
            located = _locate(text, display, cursor, reference.occurrence) if display else None
            if located is not None:
                # Whatever the markup cites, the text it covers is not prose: a
                # citation the API cannot resolve must not be re-read as one it can.
                position, end = located
                cursor = end
                guards.append(
                    Guard(
                        position,
                        end,
                        items[0]["book"] if items else None,
                        items[-1]["chapter"] if items else None,
                    )
                )
                for item in items:
                    found.append((position, order, {"text": text[position:end], **item}))
                    order += 1
            else:
                for item in items:
                    found.append((cursor, order, item))
                    order += 1
            if items:
                context = _Context(items[0]["book"], items[-1]["chapter"])
        for position, item in self.scanner.scan(text, book=book, chapter=chapter, guards=guards):
            found.append((position, order, item))
            order += 1
        found.sort(key=lambda entry: (entry[0], entry[1]))
        return _dedupe([item for _position, _order, item in found])


def _locate(text: str, display: str, start: int, occurrence: int = 0) -> tuple[int, int] | None:
    """Where the display text of a markup reference stands in the text.

    The markup may break a citation across a line where the text does; the words are
    matched with any white space between them. The markup says how many times the
    display occurs before the citation, and that occurrence is taken when it lies at
    or after ``start``, where the previous citation ended: the "1:1" of "Gen 1:1 and
    1:1" is the second one. Failing that, the first occurrence from ``start``.
    """
    pattern = re.compile(r"\s+".join(re.escape(word) for word in display.split()))
    matches = [match for match in pattern.finditer(text) if match.end() > start]
    if not matches:
        return None
    candidates = [match for match in matches if match.start() >= start]
    if not candidates:
        return None
    before = len(matches) - len(candidates)
    chosen = candidates[occurrence - before] if 0 <= occurrence - before < len(candidates) else None
    match = chosen or candidates[0]
    return match.start(), match.end()


def _dedupe(items: Iterable[dict]) -> list[dict]:
    seen: set[tuple] = set()
    result: list[dict] = []
    for item in items:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


__all__ = [
    "BookAliases",
    "Guard",
    "MarkupReference",
    "ProseScanner",
    "ReferenceEngine",
    "ReferenceResolver",
    "Span",
    "split_ordinal",
]
