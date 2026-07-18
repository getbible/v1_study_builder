# v1 Study Builder repository guide

Read this file before changing the extraction boundary, generated API shape, or
publication workflow.

## Identity and responsibility

- Product and repository: `v1_study_builder`.
- License: GPL-2.0-only. New source files use the matching SPDX identifier.
- Runtime: Python 3.12.
- Extractor: the separately released `getbiblesword` executable, pinned in
  `conf/getbiblesword.json` and invoked only as a subprocess.
- Outputs: static JSON trees for `v1_commentaries` and `v1_dictionaries`.

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
retained in the internal source record. A missing footer, failed digest, failed
artifact, unsupported major contract, or extractor error blocks all publication.

## API stability

Commentary files remain addressable by GetBible book number and chapter. Dictionary
Strong's keys remain compatible with Bible API v3 (`G3056`, `H0430`). Any breaking
path or document change requires a new API version; do not silently mutate v1.
Repeated dictionary keys retain the unsuffixed direct path for their first
definition; later definitions use deterministic `--2`, `--3`, and subsequent
suffixes and must all remain discoverable through `keys.json`.

Generated repositories are replace-only outputs. A partial `--module` build may be
used for tests but must never be pushed.

## Verification

Run before publishing changes:

```sh
python -m pip install -e '.[dev]'
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest
```

The live integration workflow additionally installs the pinned release and builds
Clarke, TSK, MHCC, StrongsGreek, StrongsHebrew, and Easton from CrossWire packages.

## Releases and secrets

The pinned `getbible/getbiblesword` release is public and must install without a
repository token. The smoke and integration workflows intentionally exercise that
unauthenticated path. The full-build workflow generates output regardless of
publication credentials and pushes only when its complete signing/SSH secret set is
available. Never print, commit, cache, or package any token or key.
