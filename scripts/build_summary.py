#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any

REPORT_STATUSES = frozenset({"running", "success", "partial", "failed"})
SUMMARY_ROW_LIMIT = 100
SUMMARY_CELL_LIMIT = 1200
PARTIAL_WARNING = (
    "::warning::Some modules or resources could not be updated. Review the build report; "
    "successful validated work has been preserved."
)
INCOMPLETE_WARNING = (
    "::warning::This build has no confirmed complete outcome. Review the report and "
    "recovery artifacts before using generated output."
)


def _redaction_tokens(remotes: tuple[str, ...]) -> tuple[str, ...]:
    """Cover literal logs and JSON-escaped repository URLs without printing secrets."""
    tokens = {
        token
        for remote in remotes
        if remote
        for token in (remote, json.dumps(remote, ensure_ascii=True)[1:-1])
    }
    return tuple(sorted(tokens, key=len, reverse=True))


def _redact(value: str, tokens: tuple[str, ...]) -> str:
    for token in tokens:
        value = value.replace(token, "[repository remote]")
    return value


def _cell(value: Any) -> str:
    text = str(value)
    if len(text) > SUMMARY_CELL_LIMIT:
        text = text[:SUMMARY_CELL_LIMIT] + "… (see JSON report)"
    return (
        html.escape(text)
        .replace("|", "&#124;")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("`", "&#96;")
    )


def _status(report: dict[str, Any]) -> str:
    value = report.get("status")
    return value if isinstance(value, str) and value in REPORT_STATUSES else "unavailable"


def render_summary(report: dict[str, Any]) -> str:
    """Render report data as escaped text, never as GitHub workflow commands."""
    status = _status(report)
    lines = [
        f"## Study API build: {status}",
        "",
        "Download the report artifact for the full JSON report and build log.",
        "The per-resource output artifacts preserve generated work; "
        "check the report before using them.",
        "",
    ]
    built = report.get("built")
    if isinstance(built, dict):
        lines += ["| Resource | Successfully built |", "| --- | ---: |"]
        for resource in ("commentaries", "dictionaries"):
            modules = built.get(resource, [])
            count = len(modules) if isinstance(modules, list) else 0
            lines.append(f"| {resource} | {count} |")
        lines.append("")
    for label, key in [
        ("Module failures", "failed"),
        ("Retained previous modules", "retained"),
        ("Build or publication errors", "errors"),
    ]:
        records = report.get(key, [])
        if not isinstance(records, list):
            continue
        rows = [row for row in records if isinstance(row, dict)]
        if not rows:
            continue
        lines += [
            f"### {label}",
            "",
            "| Resource | Module | Stage | Reason |",
            "| --- | --- | --- | --- |",
        ]
        for row in rows[:SUMMARY_ROW_LIMIT]:
            values = [row.get(field, "") for field in ("resource", "module", "stage", "reason")]
            lines.append("| " + " | ".join(f"`{_cell(value)}`" for value in values) + " |")
        if len(rows) > SUMMARY_ROW_LIMIT:
            lines += [
                "",
                f"{len(rows) - SUMMARY_ROW_LIMIT} additional records are in the full JSON report.",
            ]
        lines.append("")
    if status in {"unavailable", "running"}:
        lines += [
            "The build did not record a completed outcome. Inspect the workflow failure and log.",
            "",
        ]
    return "\n".join(lines) + "\n"


def summarize_build(
    work_dir: Path,
    *,
    step_summary: Path | None = None,
    remotes: tuple[str, ...] = (),
) -> str:
    """Preserve a sanitized command log and summarize an optional checkpoint report."""
    reports = work_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    tokens = _redaction_tokens(remotes)
    raw = work_dir / "build.raw.log"
    with (reports / "build.log").open("w", encoding="utf-8") as destination:
        if raw.exists():
            with raw.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    destination.write(_redact(line, tokens))
        else:
            destination.write(
                "The build command did not produce a log. See the preceding workflow steps.\n"
            )
    raw.unlink(missing_ok=True)

    report_path = reports / "latest.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        sanitized = _redact(report_path.read_text(encoding="utf-8", errors="replace"), tokens)
        # Even a truncated checkpoint must be sanitized before it becomes an artifact.
        report_path.write_text(sanitized, encoding="utf-8")
        try:
            parsed = json.loads(sanitized)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            report = parsed

    summary = render_summary(report)
    (reports / "summary.md").write_text(summary, encoding="utf-8")
    if step_summary is not None:
        with step_summary.open("a", encoding="utf-8") as output:
            output.write(summary)
    status = _status(report)
    if status == "partial":
        print(PARTIAL_WARNING)
    elif status in {"failed", "running", "unavailable"}:
        print(INCOMPLETE_WARNING)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Preserve build diagnostics for GitHub Actions")
    parser.add_argument("--work-dir", type=Path, default=Path(".work"))
    args = parser.parse_args()
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    summarize_build(
        args.work_dir,
        step_summary=Path(summary) if summary else None,
        remotes=(
            os.environ.get("STUDY_BUILDER_COMMENTARIES_REPO", ""),
            os.environ.get("STUDY_BUILDER_DICTIONARIES_REPO", ""),
        ),
    )
    # The original build step owns its exit code. Reporting must not mask or
    # reinterpret a partial/fatal build as another outcome.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
