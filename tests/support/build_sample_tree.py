#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate a complete API tree without CrossWire or the extractor.

Used by tests/nginx_config_check.sh to give the origin configuration something
real to serve. It drives the actual pipeline, so the tree it produces — catalog,
hashes.json, published schemas, and all — is exactly what a build publishes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY / "tests"))

from test_pipeline_build import (  # noqa: E402
    COMMENTARY,
    DICTIONARY,
    StubExporter,
    StubInstaller,
)

from study_builder import pipeline as pipeline_module  # noqa: E402
from study_builder.pipeline import BuildPipeline, PipelineConfig  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} TARGET_DIRECTORY", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])

    pipeline_module.ModuleInstaller = StubInstaller
    pipeline_module.SwordExporter = StubExporter
    pipeline_module.GetBibleSwordManager.ensure = lambda self, path=None: Path("/stub")
    BuildPipeline._catalog = lambda self: [COMMENTARY, DICTIONARY]

    report = BuildPipeline(
        PipelineConfig(
            root=REPOSITORY,
            work_dir=target / "work",
            dist_dir=target / "dist",
            policy_path=REPOSITORY / "conf/module_policy.json",
            books_path=REPOSITORY / "conf/book_registry.json",
            schemas_dir=REPOSITORY / "schemas",
            engine_manifest_path=REPOSITORY / "conf/getbiblesword.json",
            engine_schema_path=REPOSITORY / "schemas/getbiblesword-ndjson-v1.schema.json",
        )
    ).run()
    print(f"built {report.built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
