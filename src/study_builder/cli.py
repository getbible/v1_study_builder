from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from study_builder.catalog import CATALOG_URL, classify, load_catalog
from study_builder.engine import GetBibleSwordManager
from study_builder.http import HttpClient
from study_builder.pipeline import BuildPipeline, PipelineConfig
from study_builder.policy import ModulePolicy
from study_builder.util import reset_directory


def repository_root() -> Path:
    current = Path.cwd().resolve()
    if (current / "pyproject.toml").is_file() and (current / "conf").is_dir():
        return current
    source_checkout = Path(__file__).resolve().parents[2]
    if (source_checkout / "conf").is_dir():
        return source_checkout
    raise RuntimeError("Run study-builder from the v1_study_builder repository root")


def parser() -> argparse.ArgumentParser:
    root = repository_root()
    result = argparse.ArgumentParser(
        prog="study-builder",
        description="Build GetBible v1 commentary and dictionary static JSON APIs.",
    )
    result.add_argument("-v", "--verbose", action="store_true")
    commands = result.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Download, convert, validate, hash, and publish")
    build.add_argument("--resource", choices=("all", "commentaries", "dictionaries"), default="all")
    build.add_argument("--module", action="append", default=[], help="Build one named module")
    build.add_argument("--work-dir", type=Path, default=root / ".work")
    build.add_argument("--dist-dir", type=Path, default=root / "dist")
    build.add_argument("--policy", type=Path, default=root / "conf/module_policy.json")
    build.add_argument("--books", type=Path, default=root / "conf/book_registry.json")
    build.add_argument("--schemas", type=Path, default=root / "schemas")
    configured_engine = os.environ.get("STUDY_BUILDER_GETBIBLESWORD", "").strip()
    build.add_argument(
        "--engine", type=Path, default=Path(configured_engine) if configured_engine else None
    )
    build.add_argument("--engine-manifest", type=Path, default=root / "conf/getbiblesword.json")
    build.add_argument(
        "--engine-schema",
        type=Path,
        default=root / "schemas/getbiblesword-ndjson-v1.schema.json",
    )
    build.add_argument(
        "--commentaries-repo",
        default=os.environ.get(
            "STUDY_BUILDER_COMMENTARIES_REPO",
            "git@github.com:getbible/v1_commentaries.git",
        ),
    )
    build.add_argument(
        "--dictionaries-repo",
        default=os.environ.get(
            "STUDY_BUILDER_DICTIONARIES_REPO",
            "git@github.com:getbible/v1_dictionaries.git",
        ),
    )
    build.add_argument("--commentaries-branch", default="master")
    build.add_argument("--dictionaries-branch", default="master")
    build.add_argument("--pull", action="store_true", help="Clone/pull target repositories")
    build.add_argument("--push", action="store_true", help="Commit and push changed output")
    build.add_argument("--refresh", action="store_true", help="Redownload catalog and packages")
    build.add_argument("--offline", action="store_true", help="Use only cached downloads")
    build.add_argument(
        "--dry-run", action="store_true", help="List approved work without converting"
    )

    catalog = commands.add_parser("catalog", help="Show current CrossWire module policy decisions")
    catalog.add_argument(
        "--resource", choices=("all", "commentaries", "dictionaries"), default="all"
    )
    catalog.add_argument("--policy", type=Path, default=root / "conf/module_policy.json")
    catalog.add_argument("--json", action="store_true")

    clean = commands.add_parser("clean", help="Clear generated work and distribution directories")
    clean.add_argument("--work-dir", type=Path, default=root / ".work")
    clean.add_argument("--dist-dir", type=Path, default=root / "dist")

    engine = commands.add_parser("engine", help="Install or verify the pinned getbiblesword engine")
    engine_commands = engine.add_subparsers(dest="engine_command", required=True)
    for name in ("install", "verify"):
        command = engine_commands.add_parser(name)
        command.add_argument("--work-dir", type=Path, default=root / ".work")
        command.add_argument("--manifest", type=Path, default=root / "conf/getbiblesword.json")
        command.add_argument("--engine", type=Path)
        if name == "install":
            command.add_argument("--force", action="store_true")
    return result


def _build(args: argparse.Namespace) -> int:
    if args.offline and args.refresh:
        raise ValueError("--offline and --refresh cannot be combined")
    config = PipelineConfig(
        root=repository_root(),
        work_dir=args.work_dir.resolve(),
        dist_dir=args.dist_dir.resolve(),
        policy_path=args.policy.resolve(),
        books_path=args.books.resolve(),
        schemas_dir=args.schemas.resolve(),
        engine_manifest_path=args.engine_manifest.resolve(),
        engine_schema_path=args.engine_schema.resolve(),
        engine_path=args.engine.resolve() if args.engine else None,
        resource=args.resource,
        modules=frozenset(args.module),
        refresh=args.refresh,
        offline=args.offline,
        pull=args.pull,
        push=args.push,
        dry_run=args.dry_run,
        commentaries_repo=args.commentaries_repo,
        dictionaries_repo=args.dictionaries_repo,
        commentaries_branch=args.commentaries_branch,
        dictionaries_branch=args.dictionaries_branch,
    )
    report = BuildPipeline(config).run()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def _catalog(args: argparse.Namespace) -> int:
    modules = load_catalog(HttpClient().get_bytes(CATALOG_URL))
    policy = ModulePolicy(args.policy)
    records = []
    for module in modules:
        kind = classify(module)
        if kind is None or (args.resource != "all" and args.resource != kind):
            continue
        decision = policy.decide(module)
        records.append(
            {
                "resource": kind,
                "module": module.name,
                "language": module.language,
                "license": module.license,
                "allowed": decision.allowed,
                "reason": decision.reason,
            }
        )
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        for item in records:
            state = "ALLOW" if item["allowed"] else "SKIP "
            print(
                f"{state} {item['resource']:<12} {item['module']:<24} "
                f"{item['language']:<5} {item['reason']}"
            )
    return 0


def _clean(args: argparse.Namespace) -> int:
    root = repository_root().resolve()
    for path in (args.work_dir.resolve(), args.dist_dir.resolve()):
        if path == root or root not in path.parents:
            raise ValueError(f"Refusing to clean a directory outside the repository: {path}")
        reset_directory(path, boundary=root)
    return 0


def _engine(args: argparse.Namespace) -> int:
    manager = GetBibleSwordManager(args.manifest.resolve(), args.work_dir.resolve())
    if args.engine_command == "install":
        executable = manager.ensure(args.engine.resolve() if args.engine else None, args.force)
    else:
        executable = args.engine.resolve() if args.engine else manager.executable
    print(json.dumps(manager.verify(executable), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if arguments.command == "build":
        return _build(arguments)
    if arguments.command == "catalog":
        return _catalog(arguments)
    if arguments.command == "clean":
        return _clean(arguments)
    if arguments.command == "engine":
        return _engine(arguments)
    raise AssertionError(arguments.command)
