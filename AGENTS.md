# v1 Study Builder repository guide

Read this file before changing the extraction boundary, generated API shape, or
publication workflow.

## Identity and responsibility

- Product and repository: `v1_study_builder`.
- License: GPL-2.0-only. New source files use the matching SPDX identifier.
- Runtime: Python 3.12.
- Extractor: the separately released `getbiblesword` executable, pinned in
  `conf/getbiblesword.json` and invoked only as a subprocess.
- Outputs: static JSON trees under `v1/` in `getbible/commentaries` and
  `getbible/dictionaries`. The version lives in the folder, not the repository
  name, so a future `v2/` can be published beside it.

This repository does not build or link the CrossWire SWORD C++ engine. Changes to
that engine belong in `getbible/getbiblesword`. Do not reintroduce a local C++
exporter, Python SWORD binding, Git submodule, or raw-module parser.

## Trust boundary

The authoritative extractor contract is `getbiblesword.ndjson/v1`. The independent
consumer in `src/study_builder/contract.py` must continue to verify:

- zero-based monotonic sequence values and canonical top-level member order;
- every decoded byte envelope's size and SHA-256;
- the exact stream SHA-256 over every pre-footer line including LF;
- footer record, entry, artifact, and byte counts;
- chunk indexes plus byte-for-byte artifact reconstruction hashes;
- a supported header contract and `success: true` footer.

Never use a `utf8` convenience field as the authoritative value. Decode `base64`,
verify it, then create the public text projection. Unknown additive fields must be
retained in the internal source record. Validated entries remain disk-backed and
writers stream them; do not restore whole-module entry or commentary collections in
memory. Composed documents are streamed from the documents they embed, never built
up as one object. A missing footer, failed digest, failed artifact, unsupported major
contract, or extractor error blocks all publication.

## API stability

The published API is plain text. No document may reintroduce an `html` member, and
the builder must not grow an HTML sanitizer; the value of the text-only contract is
that no consumer has to sanitize a response.

Commentary files remain addressable by GetBible book number and chapter. Chapter `0`
is a book introduction and verse `0` a chapter introduction; neither may be dropped.
Book and whole-commentary documents embed their parts byte-for-byte, so
`book.chapters[n]` must stay identical to the chapter document served on its own —
`scripts/validate_build.py` asserts this and it is the property clients rely on.

`references` items carry Bible API v3 coordinates in both APIs. Those recognised in
plain text by `src/study_builder/references.py` additionally carry `text` (the
citation exactly as the entry's text spells it) and `ref` (the same citation with
the book restored), and may carry `verses`; an item without `verse` covers the whole
chapter. Text is never rewritten to make a citation easier to recognise. Prose is
only parsed for entries whose source markup carries no references, and only in
languages `conf/book_registry.json` lists spellings for; the same file's KJV verse
counts expand ranges that cross a chapter boundary. Adding a language or a spelling
is a registry change, not a code change.

Dictionary Strong's keys remain compatible with Bible API v3 (`G3056`, `H0430`).
Repeated dictionary keys retain the unsuffixed direct path for their first
definition; later definitions use deterministic `--2`, `--3`, and subsequent
suffixes and must all remain discoverable through `index.json`, which stays sorted
by its `search` term. Cross-references between words resolve only to keys that
exist in the same dictionary.

A module identifier may never collide with a document at the `v1/` root; see
`RESERVED_MODULE_IDS`. Any breaking path or document change requires a new API
version; do not silently mutate v1.

Generated repositories are replace-only outputs. A partial `--module` build may be
used for tests but must never be pushed.

## Commits

Commits in this repository are authored in the maintainer's name. Do not add a
`Co-Authored-By` trailer, a session link, an assistant name, or any other
tool attribution to a commit message, tag, or pull request.

## Verification

Run before publishing changes:

```sh
python -m pip install -e '.[dev]'
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest
```

The live integration workflow additionally installs the pinned release and builds
Clarke, TSK, MHCC, Luther, StrongsGreek, StrongsHebrew, and Easton from CrossWire
packages. Luther is the large-corpus memory regression module.

## Releases and secrets

The pinned `getbible/getbiblesword` release is public and must install without a
repository token. The smoke and integration workflows intentionally exercise that
unauthenticated path. The full-build workflow generates output regardless of
publication credentials and pushes only when its complete signing/SSH secret set is
available. Never print, commit, cache, or package any token or key.
