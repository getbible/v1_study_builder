# SPDX-License-Identifier: GPL-2.0-only
"""The Bible itself, read from the GetBible API, as the ground a reference is checked against.

Every scripture reference the builder publishes points into Bible API coordinates: a
book number, a chapter, and usually verses. Whether such a coordinate exists is not a
property of the module being converted, nor of this repository, but of the Bible as
the API publishes it. So the shape of the Bible — which books a versification has,
how many chapters each book has, how many verses each chapter has, and what the books
are called in a language — is read from the API's own documents:

- ``translations.json`` lists every translation with its language and versification;
- ``{translation}/books.json`` lists a translation's books with their numbers and names;
- ``{translation}.json`` carries the translation, from which only the chapter and verse
  counts are kept; ``{translation}.sha`` is its content hash.

The builder never publishes scripture text. It reads a translation once to learn its
shape, verifies the download against the published hash, keeps only the shape in its
cache, and checks the hash again on every later online build, as the API's cache
policy requires. A module is matched to translations by its versification, so a
commentary on the Vulgate is checked against a Vulgate, and takes its book names from a
translation in its own language, so a Swedish module publishes Swedish references.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from study_builder.http import HttpClient
from study_builder.util import read_json, utc_now, write_json

DEFAULT_BIBLE_API = "https://api.getbible.net/v2"
CANON_SCHEMA = "study-builder-bible-canon-v1"
DEFAULT_VERSIFICATION = "KJV"

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_ABBREVIATION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_BOOK_NUMBER = 200
MAX_CHAPTERS = 300


class BibleApiError(RuntimeError):
    """The Bible API could not be read, or what it published is not usable."""


@dataclass(frozen=True)
class Translation:
    abbreviation: str
    language: str
    versification: str
    sha: str

    @property
    def primary_language(self) -> str:
        return primary_language(self.language)


@dataclass(frozen=True)
class CanonBook:
    """One book as a translation publishes it: its name and the verses of each chapter."""

    number: int
    name: str
    verses: tuple[int, ...]
    translation: str

    @property
    def chapter_count(self) -> int:
        return len(self.verses)

    def verse_count(self, chapter: int) -> int | None:
        if 1 <= chapter <= len(self.verses):
            return self.verses[chapter - 1]
        return None


def primary_language(language: str) -> str:
    """Reduce a language tag to the subtag books are named in: ``zh-Hans`` is ``zh``."""
    value = (language or "").strip().replace("_", "-").casefold()
    return value.split("-", 1)[0] or "und"


class Canon:
    """The books a module's references are resolved against, named in its language."""

    def __init__(
        self,
        books: dict[int, CanonBook],
        names: dict[int, str],
        *,
        translations: tuple[str, ...],
        names_translation: str | None,
        language: str,
        versification: str,
    ) -> None:
        self.books = dict(sorted(books.items()))
        self.names = {number: names.get(number, book.name) for number, book in self.books.items()}
        self.translations = translations
        self.names_translation = names_translation
        self.language = language
        self.versification = versification

    def has_book(self, book: int) -> bool:
        return book in self.books

    def chapter_count(self, book: int) -> int | None:
        found = self.books.get(book)
        return found.chapter_count if found else None

    def verse_count(self, book: int, chapter: int) -> int | None:
        found = self.books.get(book)
        return found.verse_count(chapter) if found else None

    def has_chapter(self, book: int, chapter: int) -> bool:
        count = self.chapter_count(book)
        return count is not None and 1 <= chapter <= count

    def has_verse(self, book: int, chapter: int, verse: int) -> bool:
        count = self.verse_count(book, chapter)
        return count is not None and 1 <= verse <= count

    def name(self, book: int) -> str | None:
        return self.names.get(book)

    def describe(self) -> dict[str, Any]:
        """What a module's metadata says about the Bible its references were checked against."""
        return {
            "api": "getbible-v2",
            "versification": self.versification,
            "translations": list(self.translations),
            "names": self.names_translation,
            "language": self.language,
        }


class BibleApi:
    """Read the GetBible API v2 from its public host or from a directory of the same layout."""

    def __init__(
        self,
        location: str | Path = DEFAULT_BIBLE_API,
        cache_dir: Path | None = None,
        http: HttpClient | None = None,
        offline: bool = False,
    ) -> None:
        self.location = str(location).rstrip("/")
        self.remote = urlsplit(self.location).scheme in {"http", "https"}
        self.cache_dir = cache_dir
        self.http = http or HttpClient()
        self.offline = offline
        self._translations: dict[str, Translation] | None = None
        self._canons: dict[str, dict[int, CanonBook]] = {}
        self._book_names: dict[str, dict[int, str]] = {}

    # -- reading ------------------------------------------------------------

    def _read(self, relative: str) -> bytes:
        if self.remote:
            return self.http.get_bytes(f"{self.location}/{relative}")
        path = Path(self.location) / relative
        try:
            return path.read_bytes()
        except OSError as error:
            raise BibleApiError(f"Bible API document is not available: {path}") from error

    def _read_json(self, relative: str) -> Any:
        payload = self._read(relative)
        try:
            return json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BibleApiError(f"Bible API document is not JSON: {relative}") from error

    def _read_sha(self, relative: str, payload_relative: str) -> str:
        """The published hash of a document; for a local tree without one, the file's own."""
        if self.remote:
            value = self._read(relative).decode("ascii", errors="replace").strip().casefold()
        else:
            path = Path(self.location) / relative
            if path.is_file():
                value = path.read_text(encoding="ascii", errors="replace").strip().casefold()
            else:
                value = hashlib.sha1(
                    self._read(payload_relative), usedforsecurity=False
                ).hexdigest()
        if not _SHA1.fullmatch(value):
            raise BibleApiError(f"Bible API published an invalid hash at {relative}")
        return value

    def _cache_path(self, name: str) -> Path | None:
        return self.cache_dir / name if self.cache_dir else None

    # -- catalogue ----------------------------------------------------------

    def translations(self) -> dict[str, Translation]:
        if self._translations is not None:
            return self._translations
        cache = self._cache_path("translations.json")
        if self.offline:
            if cache is None or not cache.is_file():
                raise BibleApiError(
                    "Offline mode requested but no cached Bible API translations catalogue exists"
                )
            data = read_json(cache)
        else:
            data = self._read_json("translations.json")
            if cache is not None:
                write_json(cache, data)
        self._translations = _parse_translations(data)
        return self._translations

    # -- one translation ----------------------------------------------------

    def book_names(self, abbreviation: str) -> dict[int, str]:
        """The numbered book names a translation publishes in ``books.json``."""
        if abbreviation in self._book_names:
            return self._book_names[abbreviation]
        abbreviation = _abbreviation(abbreviation)
        cache = self._cache_path(f"{abbreviation}.books.json")
        if self.offline:
            if cache is None or not cache.is_file():
                raise BibleApiError(
                    f"Offline mode requested but no cached books index exists for {abbreviation}"
                )
            data = read_json(cache)
        else:
            data = self._read_json(f"{abbreviation}/books.json")
            if cache is not None:
                write_json(cache, data)
        names = _parse_books_index(data, abbreviation)
        self._book_names[abbreviation] = names
        return names

    def canon(self, abbreviation: str) -> dict[int, CanonBook]:
        """The shape of one translation: every book with the verse count of every chapter."""
        if abbreviation in self._canons:
            return self._canons[abbreviation]
        abbreviation = _abbreviation(abbreviation)
        cache = self._cache_path(f"{abbreviation}.json")
        cached = _read_canon_cache(cache, abbreviation)
        if self.offline:
            if cached is None:
                raise BibleApiError(
                    f"Offline mode requested but no cached Bible shape exists for {abbreviation}"
                )
            record = cached
        else:
            record = self._refresh(abbreviation, cached)
            if cache is not None:
                write_json(cache, record)
        books = {
            int(item["number"]): CanonBook(
                int(item["number"]),
                str(item["name"]),
                tuple(int(count) for count in item["verses"]),
                abbreviation,
            )
            for item in record["books"]
        }
        self._canons[abbreviation] = books
        return books

    def _refresh(self, abbreviation: str, cached: dict[str, Any] | None) -> dict[str, Any]:
        translation = self.translations().get(abbreviation)
        remote_sha = self._read_sha(f"{abbreviation}.sha", f"{abbreviation}.json")
        if cached is not None and cached.get("sha") == remote_sha:
            return {**cached, "checked_at": utc_now()}
        names = self.book_names(abbreviation)
        payload: bytes | None = None
        for _attempt in range(2):
            payload = self._read(f"{abbreviation}.json")
            actual = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
            if actual == remote_sha:
                break
            # The translation was republished between the two reads; read both again.
            remote_sha = self._read_sha(f"{abbreviation}.sha", f"{abbreviation}.json")
            if actual == remote_sha:
                break
            payload = None
        if payload is None:
            raise BibleApiError(
                f"Bible API translation {abbreviation} does not match its published hash"
            )
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BibleApiError(f"Bible API translation {abbreviation} is not JSON") from error
        books = _shape_of(document, abbreviation)
        published = {number: book["name"] for number, book in books.items()}
        if published != names:
            raise BibleApiError(
                f"Bible API translation {abbreviation} does not match its own books index"
            )
        return {
            "schema": CANON_SCHEMA,
            "translation": abbreviation,
            "sha": remote_sha,
            "checked_at": utc_now(),
            "language": translation.language if translation else "",
            "versification": translation.versification if translation else "",
            "books": [books[number] for number in sorted(books)],
        }

    # -- choosing translations for a module -----------------------------------

    def select(self, language: str, versification: str) -> tuple[list[str], str | None]:
        """Choose the translations that give a module its shape and its book names.

        The shape comes from the translations that share the module's versification: the
        best match of the versification itself, plus the best match of its Apocrypha
        variant (``KJVA`` beside ``KJV``) so that deuterocanonical citations resolve. When
        the API has no translation in that versification the KJV family stands in, and
        when it has not even that, a translation in the module's language, and failing
        that the first translation in the catalogue. Book names come from a translation
        in the module's own language, preferring one that also matches the shape.
        """
        catalogue = self.translations()
        if not catalogue:
            raise BibleApiError("The Bible API catalogue lists no translations")
        lang = primary_language(language)
        wanted = (versification or DEFAULT_VERSIFICATION).strip() or DEFAULT_VERSIFICATION

        def family(name: str) -> list[Translation]:
            return [t for t in catalogue.values() if t.versification.casefold() == name.casefold()]

        def best(candidates: list[Translation], name: str) -> Translation | None:
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda t: (
                    t.primary_language != lang,
                    t.abbreviation != name.casefold(),
                    t.abbreviation,
                ),
            )

        chosen = [best(family(wanted), wanted), best(family(wanted + "A"), wanted + "A")]
        if not any(chosen) and wanted.casefold() != DEFAULT_VERSIFICATION.casefold():
            chosen = [
                best(family(DEFAULT_VERSIFICATION), DEFAULT_VERSIFICATION),
                best(family(DEFAULT_VERSIFICATION + "A"), DEFAULT_VERSIFICATION + "A"),
            ]
        shape = [t.abbreviation for t in chosen if t is not None]
        same_language = sorted(
            (t for t in catalogue.values() if t.primary_language == lang),
            key=lambda t: t.abbreviation,
        )
        if not shape:
            shape = [same_language[0].abbreviation if same_language else sorted(catalogue)[0]]
        names = next((t for t in shape if catalogue[t].primary_language == lang), None)
        if names is None and same_language:
            matching = [t for t in same_language if t.versification.casefold() == wanted.casefold()]
            names = (matching or same_language)[0].abbreviation
        return shape, names

    def canon_for(self, language: str, versification: str) -> Canon:
        shape, names_translation = self.select(language, versification)
        books: dict[int, CanonBook] = {}
        for abbreviation in shape:
            for number, book in self.canon(abbreviation).items():
                books.setdefault(number, book)
        names = self.book_names(names_translation) if names_translation else {}
        wanted = (versification or DEFAULT_VERSIFICATION).strip() or DEFAULT_VERSIFICATION
        return Canon(
            books,
            names,
            translations=tuple(shape),
            names_translation=names_translation,
            language=primary_language(language),
            versification=wanted,
        )


# -- parsing the API's documents ------------------------------------------------


def _abbreviation(value: str) -> str:
    normalized = str(value).strip().casefold()
    if not _ABBREVIATION.fullmatch(normalized):
        raise BibleApiError(f"Invalid Bible API translation abbreviation: {value!r}")
    return normalized


def _parse_translations(data: Any) -> dict[str, Translation]:
    if not isinstance(data, dict):
        raise BibleApiError("Bible API translations catalogue is not an object")
    catalogue: dict[str, Translation] = {}
    for key, item in data.items():
        if not isinstance(item, dict):
            raise BibleApiError(f"Bible API translation {key!r} is not an object")
        abbreviation = _abbreviation(str(item.get("abbreviation") or key))
        catalogue[abbreviation] = Translation(
            abbreviation=abbreviation,
            language=str(item.get("lang") or ""),
            versification=str(item.get("distribution_versification") or "").strip(),
            sha=str(item.get("sha") or "").strip().casefold(),
        )
    return catalogue


def _book_number(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_BOOK_NUMBER:
        raise BibleApiError(f"{context} carries an invalid book number: {value!r}")
    return value


def _parse_books_index(data: Any, abbreviation: str) -> dict[int, str]:
    if not isinstance(data, dict) or not data:
        raise BibleApiError(f"Bible API books index for {abbreviation} is not a non-empty object")
    names: dict[int, str] = {}
    for key, item in data.items():
        if not isinstance(item, dict):
            raise BibleApiError(f"Bible API books index for {abbreviation} has a non-object book")
        number = _book_number(item.get("nr"), f"Bible API books index for {abbreviation}")
        if str(number) != str(key).strip():
            raise BibleApiError(
                f"Bible API books index for {abbreviation} keys book {number} as {key!r}"
            )
        name = str(item.get("name") or "").strip()
        if not name:
            raise BibleApiError(f"Bible API books index for {abbreviation} has an unnamed book")
        names[number] = name
    return names


def _shape_of(document: Any, abbreviation: str) -> dict[int, dict[str, Any]]:
    """Reduce a translation document to the counts the builder keeps; the text is dropped."""
    if not isinstance(document, dict) or not isinstance(document.get("books"), list):
        raise BibleApiError(f"Bible API translation {abbreviation} carries no books array")
    books: dict[int, dict[str, Any]] = {}
    for book in document["books"]:
        if not isinstance(book, dict):
            raise BibleApiError(f"Bible API translation {abbreviation} has a non-object book")
        number = _book_number(book.get("nr"), f"Bible API translation {abbreviation}")
        if number in books:
            raise BibleApiError(f"Bible API translation {abbreviation} repeats book {number}")
        name = str(book.get("name") or "").strip()
        chapters = book.get("chapters")
        if not name or not isinstance(chapters, list) or not chapters:
            raise BibleApiError(
                f"Bible API translation {abbreviation} book {number} has no name or chapters"
            )
        if len(chapters) > MAX_CHAPTERS:
            raise BibleApiError(
                f"Bible API translation {abbreviation} book {number} has too many chapters"
            )
        counts: list[int] = []
        for index, chapter in enumerate(chapters, start=1):
            if not isinstance(chapter, dict) or chapter.get("chapter") != index:
                raise BibleApiError(
                    f"Bible API translation {abbreviation} book {number} chapters are not "
                    "numbered consecutively from one"
                )
            verses = chapter.get("verses")
            if not isinstance(verses, list):
                raise BibleApiError(
                    f"Bible API translation {abbreviation} book {number} chapter {index} "
                    "has no verses array"
                )
            numbers = [item.get("verse") if isinstance(item, dict) else None for item in verses]
            if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in numbers):
                raise BibleApiError(
                    f"Bible API translation {abbreviation} book {number} chapter {index} "
                    "has an invalid verse number"
                )
            # Verse numbers are trusted to be dense; the count is the highest number, so a
            # translation that omits a verse still lets a reference to the last verse resolve.
            counts.append(max(numbers) if numbers else 0)
        books[number] = {"number": number, "name": name, "verses": counts}
    if not books:
        raise BibleApiError(f"Bible API translation {abbreviation} has no books")
    return books


def _read_canon_cache(path: Path | None, abbreviation: str) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        record = read_json(path)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("schema") != CANON_SCHEMA
        or record.get("translation") != abbreviation
        or not isinstance(record.get("sha"), str)
        or not _SHA1.fullmatch(record["sha"])
        or not isinstance(record.get("books"), list)
    ):
        return None
    try:
        for item in record["books"]:
            _book_number(item["number"], "cached Bible shape")
            if not str(item["name"]) or not all(
                isinstance(count, int) and count >= 0 for count in item["verses"]
            ):
                return None
    except (KeyError, TypeError):
        return None
    return record


__all__ = [
    "DEFAULT_BIBLE_API",
    "BibleApi",
    "BibleApiError",
    "Canon",
    "CanonBook",
    "Translation",
    "primary_language",
]
