# SPDX-License-Identifier: GPL-2.0-only
"""Recognise scripture references written inside plain text.

Some SWORD modules mark every scripture reference up, and the builder publishes
those as ``references`` directly. Many dictionaries, and a few commentaries,
only carry them as prose — ``son of Adam, slain by Cain Ge 4:2,8; Mt 23:35;
Heb 11:4; 12:24`` — which left every client to write its own parser before a
word could be linked back to the Bible. This module recognises those citations,
resolves each one to GetBible book, chapter, and verse numbers, and leaves the
text itself untouched.

The conventions are the ones the source modules use:

- a citation starts with a book name, in any spelling the book registry lists
  for the module's language, followed by a chapter and usually a verse;
- ``,`` continues a list of verses, ``-`` spans a range, and ``;`` starts the
  next chapter of the same book, so ``Heb 11:4; 12:24`` cites Hebrews twice;
- a chapter and verse with no book at all — ``12:24``, or ``In 9:46 he is
  called`` — belongs to the book named most recently, in text order;
- ``ver. 7`` belongs to the chapter cited most recently.

Every recognised citation is published with the exact text it was recognised
from, so a client can find it again, and with ``ref`` restoring the book name
the source left implicit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from study_builder.books import Book, BookRegistry

_NUM = r"\d{1,3}"
# U+FFFD stands where the extractor replaced a byte it could not decode; in every
# module seen so far that byte was the non-breaking space of "1\u00a0Chronicles".
_SP_CHARS = " \t\u00a0\u2009\u202f\ufffd"
_SP = f"[{_SP_CHARS}]"
_DASH = "[-\u2010\u2011\u2012\u2013\u2014\u2212]"
# Latin letters, including the accented forms Swedish and Vietnamese names use.
_LETTER = "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff"
# A book name may follow a lowercase word directly — a Vietnamese gloss runs straight
# into "Lu 3:38" — but not an uppercase one, which is a heading such as "REVERENCEGe".
_UPPER = "A-Z\u00c0-\u00d6\u00d8-\u00de"
# Five ordinals, not four: Swedish counts the books of Moses, so "5 Mos" is Deuteronomy.
_ORDINALS = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_ORDINAL_PREFIX = re.compile("^(IV|III|II|I|V|[1-5])[ \u00a0]+(.+)$")

# A number that ends here: the next character is not another digit.
_HEAD = re.compile(rf"(?P<chapter>{_NUM})(?:(?:{_SP}*:{_SP}*|\.)(?P<verse>{_NUM}))?(?!\d)")
_RANGE = (
    rf"(?:{_SP}*{_DASH}{_SP}*(?:(?P<end_chapter>{_NUM})[:.])?(?P<end_verse>{_NUM})(?!\d)"
    rf"|{_SP}+to{_SP}+(?P<to_chapter>{_NUM}):(?P<to_verse>{_NUM})(?!\d))?"
)
# "8:1 ff" and "12:5 f." cite the verse and those that follow it; the suffix is kept
# in the text but adds no verse, since where the passage ends is not stated.
_SUFFIX = rf"(?:{_SP}?ff?(?![{_LETTER}])\.?)?"
_VERSE_TAIL = re.compile(_RANGE + _SUFFIX)
_VERSE_MORE = re.compile(
    rf"{_SP}*,{_SP}*(?:(?P<chapter>{_NUM})[:.])?(?P<verse>{_NUM})(?!\d)" + _RANGE + _SUFFIX
)
# A bare chapter is only continued when what follows cannot be prose: "Ex 28; 29;
# Le 8" continues, "Ps 99:6; 4 sons of" does not.
_DELIMITED = rf"(?={_SP}*(?:[^{_LETTER}\d{_SP_CHARS}]|$))"
_CHAPTER_TAIL = re.compile(rf"(?:{_SP}*{_DASH}{_SP}*(?P<end_chapter>{_NUM})(?!\d)(?![:.]\d))?")
_CHAPTER_MORE = re.compile(
    rf"{_SP}*,{_SP}*(?P<chapter>{_NUM})(?:[:.](?P<verse>{_NUM})(?!\d)|(?!\d)(?![:.]\d){_DELIMITED})"
)
_NEXT = re.compile(
    rf"{_SP}*;{_SP}*(?P<chapter>{_NUM})(?:[:.](?P<verse>{_NUM})(?!\d)|(?!\d)(?![:.]\d){_DELIMITED})"
)
_PROSE = re.compile(
    rf"(?<![{_LETTER}\d:.,/-])(?P<chapter>{_NUM}):(?P<verse>{_NUM})(?!\d)"
    rf"(?!{_SP}*(?:[ap]\.?m(?![{_LETTER}])|o['’]clock))"
)
_VERSE_ONLY = re.compile(
    rf"(?<![{_LETTER}])(?:(?:vv|ver|verses|verse)\.?{_SP}?|v\.{_SP})(?P<verse>{_NUM})(?!\d)"
    rf"(?:{_SP}*{_DASH}{_SP}*(?P<end_verse>{_NUM})(?!\d))?"
    rf"(?P<more>(?:{_SP}*,{_SP}*{_NUM}(?!\d)(?:{_SP}*{_DASH}{_SP}*{_NUM}(?!\d))?)*)",
    re.IGNORECASE,
)
_VERSE_ITEM = re.compile(rf"({_NUM})(?:{_SP}*{_DASH}{_SP}*({_NUM}))?")
_SENSE = re.compile(rf"\.{_SP}+\d")
_LETTER_AT = re.compile(rf"[{_LETTER}]")
_PRECEDING_WORD = re.compile(rf"([{_LETTER}][{_LETTER}'’.\-]*)\.?{_SP}*$")

# A chapter and verse without a book is only a scripture reference when it is not
# the citation of some other work. Any capitalised word directly before it that is
# not one of these connectives — "Ant. 11:8", "Enoch 6:6", "Hebrew 9:4" — names such
# a work, so the citation is left alone.
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
        "also",
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
        "only",
        "again",
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
        "jfr",
        "se",
        "och",
        "samt",
        "även",
        "jämför",
        "xem",
        "và",
        "sánh",
        "trong",
    }
)

WHOLE_CHAPTER = object()


@dataclass(frozen=True)
class _Span:
    """One passage inside a citation, as written: a chapter, a verse, or a range."""

    chapter: int
    verse: int | None = None
    end_chapter: int | None = None
    end_verse: int | None = None


@dataclass
class _Context:
    """What an unqualified citation inherits: the book, and the chapter, last named."""

    book: Book | None = None
    book_text: str = ""
    chapter: int | None = None


def split_ordinal(name: str) -> tuple[int | None, str]:
    """Separate "1 Sam", "I Sam", or "1 Sam" into an ordinal and a bare name."""
    match = _ORDINAL_PREFIX.match(name.strip())
    if not match:
        return None, name.strip()
    return _ORDINALS[match.group(1)], match.group(2).strip()


def _name_pattern(name: str) -> str:
    # The first letter must be written as listed; the rest may vary in case, so
    # "GENESIS 1:1" is recognised but a lowercase "is 6:1" is left alone.
    words = []
    for word in name.split(" "):
        rest = f"(?i:{re.escape(word[1:])})" if len(word) > 1 else ""
        words.append(re.escape(word[0]) + rest)
    return f"{_SP}+".join(words)


class ReferenceParser:
    """Recognise the scripture references written in one language's conventions."""

    def __init__(self, books: BookRegistry, language: str) -> None:
        self.books = books
        self.language = language.split("-", 1)[0].casefold()
        self._lookup: dict[tuple[int | None, str], Book] = {}
        spellings: dict[str, str] = {}
        for book in books.books:
            for name in book.names.get(self.language, ()):
                ordinal, bare = split_ordinal(name.rstrip("."))
                if not bare:
                    continue
                self._lookup.setdefault((ordinal, bare.casefold()), book)
                spellings.setdefault(bare.casefold(), bare)
        ordered = sorted(spellings.values(), key=lambda value: (-len(value), value))
        self._pattern = (
            re.compile(
                rf"(?<![{_UPPER}])(?:(?P<ordinal>IV|III|II|I|V|[1-5]){_SP}*\n?{_SP}*)?"
                rf"(?P<name>{'|'.join(_name_pattern(name) for name in ordered)})"
                rf"(?:\.{_SP}*|{_SP}+|(?={_SP}*\n))(?=\n?{_SP}*[(\[]?{_SP}*\d)"
            )
            if ordered
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._pattern is not None

    def extract(
        self, text: str, *, book: int | None = None, chapter: int | None = None
    ) -> list[dict[str, Any]]:
        """Return every scripture reference recognised in ``text``, in text order.

        ``book`` and ``chapter`` seed what an unqualified citation inherits before
        the text names any book itself: a commentary on John may say ``3:16`` and
        mean its own book.
        """
        if not self._pattern:
            return []
        context = _Context()
        if book is not None and book in self.books.by_number:
            context.book = self.books.by_number[book]
            context.book_text = context.book.name
            context.chapter = chapter if chapter else None
        return _Scanner(self, text, context).run()

    def find_book(self, text: str, position: int) -> tuple[int, re.Match[str], Book] | None:
        """Find the next book name at or after ``position``.

        A number before a name that stands on its own — the "2" in "Ps 1:2 Mt 3:4"
        — is a verse of the previous citation, not an ordinal, so the name is used
        alone. A name that needs an ordinal keeps it even when a digit precedes,
        because a Vietnamese Strong's number runs straight into "2 Cô 8:15".
        """
        if not self._pattern:
            return None
        while True:
            match = self._pattern.search(text, position)
            if not match:
                return None
            ordinal = match.group("ordinal")
            name = match.group("name").casefold()
            bare = self._lookup.get((None, name))
            book = self._lookup.get((_ORDINALS[ordinal], name)) if ordinal else None
            start = match.start()
            after_digit = start > 0 and text[start - 1].isdigit()
            if book is None or (after_digit and bare is not None):
                book = bare
                start = match.start("name")
            if book is not None:
                return start, match, book
            position = match.start() + 1


class _Scanner:
    def __init__(self, parser: ReferenceParser, text: str, context: _Context) -> None:
        self.parser = parser
        self.books = parser.books
        self.text = text
        self.context = context
        self.found: list[dict[str, Any]] = []

    def run(self) -> list[dict[str, Any]]:
        position = 0
        length = len(self.text)
        while position < length:
            candidates: list[tuple[int, str, Any]] = []
            book = self.parser.find_book(self.text, position)
            if book:
                candidates.append((book[0], "book", book))
            prose = _PROSE.search(self.text, position)
            if prose:
                candidates.append((prose.start(), "prose", prose))
            verse_only = _VERSE_ONLY.search(self.text, position)
            if verse_only:
                candidates.append((verse_only.start(), "verse", verse_only))
            if not candidates:
                break
            start, kind, match = min(candidates, key=lambda item: item[0])
            if kind == "book":
                position = self._book_citation(*match)
            elif kind == "prose":
                position = self._prose_citation(match)
            else:
                position = self._verse_citation(match)
        return self.found

    # -- citation forms -----------------------------------------------------

    def _book_citation(self, start: int, match: re.Match[str], book: Book) -> int:
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
        inner = self.parser.find_book(self.text, position)
        if inner is not None and inner[0] == position:
            # "Ezra (2 Esdras 1:2)": the number is the ordinal of the next book, not
            # a chapter of this one, so this name is not a citation at all.
            return match.end()
        head = _HEAD.match(self.text, position)
        if head is None:  # unreachable: the name pattern looks ahead for a digit
            return match.end()
        if head.group("verse") is None and (
            wrapped
            or _SENSE.match(self.text, head.end())
            or _LETTER_AT.match(self.text, head.end())
        ):
            # "Prov. 2. 2. Fig.:" numbers the senses of a general dictionary and
            # "John 3rd" is not John 3; neither is a chapter alone after a line
            # break, which is far more often a numbered sense.
            return match.end()
        end, spans = self._spans(head)
        book_text = self.text[start : match.end()].rstrip(_SP_CHARS)
        self.context = _Context(book, book_text, spans[-1].chapter if spans else None)
        self._emit(start if explicit else position, end, spans, explicit=explicit)
        return self._continuations(end, emit=True)

    def _prose_citation(self, match: re.Match[str]) -> int:
        if self.context.book is None or not self._plain_before(match.start()):
            # Nothing to inherit, or the citation of another work: skip it and
            # whatever continues it, so "Enoch 6:6; 8:1" does not leak its tail.
            return self._continuations(match.end(), emit=False)
        end, spans = self._spans(_HEAD.match(self.text, match.start()))
        self.context.chapter = spans[-1].chapter if spans else self.context.chapter
        self._emit(match.start(), end, spans, explicit=False)
        return self._continuations(end, emit=True)

    def _verse_citation(self, match: re.Match[str]) -> int:
        context = self.context
        if context.book is None or context.chapter is None:
            return match.end()
        if not self._plain_before(match.start()):
            return match.end()
        spans = [_Span(context.chapter, int(match.group("verse")), None, _end(match, "end_verse"))]
        for verse, end_verse in _VERSE_ITEM.findall(match.group("more") or ""):
            spans.append(_Span(context.chapter, int(verse), None, int(end_verse or 0) or None))
        items = self._resolve(context.book, spans)
        if items:
            cited = self.text[match.start() : match.end()]
            numbers = cited[match.start("verse") - match.start() :]
            reference = f"{context.book_text} {context.chapter}:{numbers}"
            self.found.extend({"text": cited, "ref": reference, **item} for item in items)
        return match.end()

    def _continuations(self, position: int, *, emit: bool) -> int:
        """Consume "; 12:24" and "; 29" chunks that continue the current book."""
        while True:
            match = _NEXT.match(self.text, position)
            if match is None:
                return position
            end, spans = self._spans(_HEAD.match(self.text, match.start("chapter")))
            if emit:
                self.context.chapter = spans[-1].chapter if spans else self.context.chapter
                self._emit(match.start("chapter"), end, spans, explicit=False)
            position = end

    # -- pieces ---------------------------------------------------------------

    def _skip_spaces(self, position: int) -> int:
        while position < len(self.text) and self.text[position] in _SP_CHARS:
            position += 1
        return position

    def _plain_before(self, position: int) -> bool:
        match = _PRECEDING_WORD.search(self.text, 0, position)
        if match is None:
            return True
        word = match.group(1).strip(".'’-")
        return not word[:1].isupper() or word.casefold() in _CONNECTIVES

    def _spans(self, head: re.Match[str]) -> tuple[int, list[_Span]]:
        """Read one citation chunk from its first number to where the numbers stop."""
        chapter = int(head.group("chapter"))
        verse = _end(head, "verse")
        end = head.end()
        spans: list[_Span] = []
        if verse is not None:
            tail = _VERSE_TAIL.match(self.text, end)
            spans.append(_range_span(chapter, verse, tail))
            end = tail.end()
        else:
            tail = _CHAPTER_TAIL.match(self.text, end)
            spans.append(_Span(chapter, None, _end(tail, "end_chapter"), None))
            end = tail.end()
        while True:
            if verse is not None:
                more = _VERSE_MORE.match(self.text, end)
                if more is None:
                    break
                chapter = _end(more, "chapter") or chapter
                spans.append(_range_span(chapter, int(more.group("verse")), more))
            else:
                more = _CHAPTER_MORE.match(self.text, end)
                if more is None:
                    break
                chapter = int(more.group("chapter"))
                verse = _end(more, "verse")
                if verse is not None:
                    tail = _VERSE_TAIL.match(self.text, more.end())
                    spans.append(_range_span(chapter, verse, tail))
                    end = tail.end()
                    continue
                spans.append(_Span(chapter))
            end = more.end()
        return end, spans

    def _emit(self, start: int, end: int, spans: list[_Span], *, explicit: bool) -> None:
        book = self.context.book
        if book is None:
            return
        items = self._resolve(book, spans)
        if not items:
            return
        cited = self.text[start:end]
        reference = cited if explicit else f"{self.context.book_text} {cited}"
        self.found.extend({"text": cited, "ref": reference, **item} for item in items)

    def _resolve(self, book: Book, spans: list[_Span]) -> list[dict[str, Any]]:
        """Turn the passages of one citation into GetBible coordinates, one per chapter.

        A range that crosses a chapter boundary is expanded with the verse counts of
        the registry, so "Ex 4:1-16:36" names every chapter it covers; without a
        count, only the verse it starts at can be stated.
        """
        chapters: dict[int, set[int] | object] = {}

        def whole(chapter: int) -> None:
            chapters[chapter] = WHOLE_CHAPTER

        def verses(chapter: int, numbers: range | list[int]) -> None:
            current = chapters.get(chapter)
            if current is WHOLE_CHAPTER:
                return
            if current is None:
                current = chapters[chapter] = set()
            assert isinstance(current, set)
            current.update(numbers)

        count = book.chapter_count
        for span in spans:
            if span.chapter < 1 or (count is not None and span.chapter > count):
                continue
            if span.verse is None:
                last = span.end_chapter or span.chapter
                if count is not None:
                    last = min(last, count)
                for chapter in range(span.chapter, max(last, span.chapter) + 1):
                    whole(chapter)
                continue
            if span.verse < 1:
                continue
            if span.end_chapter is None or span.end_chapter == span.chapter:
                last = span.end_verse if span.end_verse and span.end_verse >= span.verse else None
                limit = book.verse_count(span.chapter)
                if last is not None and limit is not None:
                    last = min(last, limit)
                verses(span.chapter, range(span.verse, max(last or span.verse, span.verse) + 1))
                continue
            limit = book.verse_count(span.chapter)
            if (
                span.end_chapter < span.chapter
                or (count is not None and span.end_chapter > count)
                or limit is None
            ):
                verses(span.chapter, [span.verse])
                continue
            verses(span.chapter, range(span.verse, max(limit, span.verse) + 1))
            for chapter in range(span.chapter + 1, span.end_chapter):
                whole(chapter)
            last = max(span.end_verse or 1, 1)
            end_limit = book.verse_count(span.end_chapter)
            if end_limit is not None:
                last = min(last, end_limit)
            verses(span.end_chapter, range(1, last + 1))

        osis = book.osis[0]
        items: list[dict[str, Any]] = []
        for chapter, covered in chapters.items():
            if covered is WHOLE_CHAPTER:
                items.append({"osis": f"{osis}.{chapter}", "book": book.number, "chapter": chapter})
                continue
            assert isinstance(covered, set)
            numbers = sorted(covered)
            item: dict[str, Any] = {
                "osis": f"{osis}.{chapter}.{numbers[0]}",
                "book": book.number,
                "chapter": chapter,
                "verse": numbers[0],
            }
            if len(numbers) > 1:
                item["verses"] = numbers
            items.append(item)
        return items


def _end(match: re.Match[str] | None, group: str) -> int | None:
    if match is None:
        return None
    value = match.group(group)
    return int(value) if value else None


def _range_span(chapter: int, verse: int, tail: re.Match[str]) -> _Span:
    end_chapter = _end(tail, "end_chapter") or _end(tail, "to_chapter")
    end_verse = _end(tail, "end_verse") or _end(tail, "to_verse")
    return _Span(chapter, verse, end_chapter, end_verse)


__all__ = ["ReferenceParser", "split_ordinal"]
