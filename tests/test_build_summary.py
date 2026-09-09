# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_summary.py"


def _run_summary(
    tmp_path: Path,
    report: object = None,
    *,
    raw_report: str | None = None,
    log: str | None = None,
    remote: str = "",
) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    work = tmp_path / ".work"
    reports = work / "reports"
    reports.mkdir(parents=True)
    if raw_report is not None:
        (reports / "latest.json").write_text(raw_report, encoding="utf-8")
    elif report is not None:
        (reports / "latest.json").write_text(json.dumps(report), encoding="utf-8")
    if log is not None:
        (work / "build.raw.log").write_text(log, encoding="utf-8")
    step_summary = tmp_path / "step-summary.md"
    step_summary.write_text("Earlier step\n", encoding="utf-8")
    environment = {
        **os.environ,
        "GITHUB_STEP_SUMMARY": str(step_summary),
        "STUDY_BUILDER_COMMENTARIES_REPO": remote,
        "STUDY_BUILDER_DICTIONARIES_REPO": "",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--work-dir", str(work)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return work, step_summary, result


@pytest.mark.parametrize("status", ["partial", "failed", "success", "running"])
def test_summary_preserves_outcome_and_details(tmp_path: Path, status: str) -> None:
    report = {
        "status": status,
        "built": {"commentaries": ["Clarke"], "dictionaries": []},
        "failed": [
            {
                "resource": "commentaries",
                "module": "Sentiment",
                "stage": "references",
                "reason": "Unavailable Bible shape",
            }
        ],
        "retained": [
            {
                "resource": "commentaries",
                "module": "Sentiment",
                "reason": "Preserved previous verified output",
            }
        ],
        "errors": [
            {
                "resource": "dictionaries",
                "stage": "publication",
                "reason": "Push failed",
            }
        ],
    }
    work, step_summary, result = _run_summary(tmp_path, report, log="Build log\n")
    reports = work / "reports"
    summary = (reports / "summary.md").read_text(encoding="utf-8")
    assert f"## Study API build: {status}" in summary
    assert "| commentaries | 1 |" in summary
    assert "Module failures" in summary
    assert "Retained previous modules" in summary
    assert "Build or publication errors" in summary
    assert "`Sentiment`" in summary
    assert "`references`" in summary
    assert "`Push failed`" in summary
    assert step_summary.read_text(encoding="utf-8") == "Earlier step\n" + summary
    assert (reports / "build.log").read_text(encoding="utf-8") == "Build log\n"
    assert json.loads((reports / "latest.json").read_text()) == report
    assert not (work / "build.raw.log").exists()
    assert ("::warning::" in result.stdout) is (status != "success")
    assert result.stderr == ""


def test_missing_report_still_produces_downloadable_diagnostics(tmp_path: Path) -> None:
    work, step_summary, result = _run_summary(tmp_path)
    reports = work / "reports"
    assert not (reports / "latest.json").exists()
    summary = (reports / "summary.md").read_text(encoding="utf-8")
    assert "Study API build: unavailable" in summary
    assert "did not record a completed outcome" in summary
    assert "did not produce a log" in (reports / "build.log").read_text(encoding="utf-8")
    assert summary in step_summary.read_text(encoding="utf-8")
    assert result.stdout.startswith("::warning::")


@pytest.mark.parametrize(
    "remote",
    [
        "https://builder:private-token@github.com/example/output.git",
        'https://builder:private-"token@github.com/example/output.git',
    ],
)
def test_credentials_are_redacted_from_all_diagnostics(tmp_path: Path, remote: str) -> None:
    report = {
        "status": "failed",
        "built": {"commentaries": ["Clarke"]},
        "errors": [{"stage": "publication", "reason": f"Failed to push {remote}"}],
    }
    work, step_summary, result = _run_summary(
        tmp_path, report, log=f"Running git push {remote}\nOther detail\n", remote=remote
    )
    for path in [*(work / "reports").iterdir(), step_summary]:
        content = path.read_text(encoding="utf-8")
        assert remote not in content
        assert "private-" not in content
    assert remote not in result.stdout + result.stderr
    assert "[repository remote]" in (work / "reports" / "build.log").read_text()
    sanitized = json.loads((work / "reports" / "latest.json").read_text())
    assert sanitized["errors"][0]["reason"] == "Failed to push [repository remote]"
    assert not (work / "build.raw.log").exists()


def test_truncated_report_is_sanitized_without_fabricating_success(tmp_path: Path) -> None:
    remote = "https://builder:private-token@github.com/example/output.git"
    work, _, result = _run_summary(tmp_path, raw_report='{"reason": "' + remote, remote=remote)
    reports = work / "reports"
    assert "private-token" not in (reports / "latest.json").read_text()
    assert "unavailable" in (reports / "summary.md").read_text()
    assert result.stdout.startswith("::warning::")


@pytest.mark.parametrize(
    "report",
    [
        [],
        {"status": [], "built": None, "failed": [None, "invalid"]},
        {"status": "unexpected", "built": {"commentaries": "invalid"}, "errors": "invalid"},
    ],
)
def test_malformed_report_shape_cannot_break_summary(tmp_path: Path, report: object) -> None:
    work, _, result = _run_summary(tmp_path, report)
    assert "unavailable" in (work / "reports" / "summary.md").read_text()
    assert result.stdout.startswith("::warning::")


def test_report_content_cannot_inject_markdown_or_workflow_commands(tmp_path: Path) -> None:
    report = {
        "status": "partial",
        "failed": [
            {
                "module": "Module|`<script>",
                "reason": "broken\n::error::injected",
            }
        ],
    }
    work, _, result = _run_summary(tmp_path, report)
    summary = (work / "reports" / "summary.md").read_text(encoding="utf-8")
    assert "Module&#124;&#96;&lt;script&gt;" in summary
    assert "broken ::error::injected" in summary
    assert "\n::error::" not in summary
    assert "::error::" not in result.stdout + result.stderr
    assert result.stdout.count("::warning::") == 1


def test_large_summary_keeps_full_report_as_evidence(tmp_path: Path) -> None:
    report = {
        "status": "partial",
        "failed": [{"module": f"Module{index}", "reason": "x" * 2000} for index in range(101)],
    }
    work, _, _ = _run_summary(tmp_path, report)
    reports = work / "reports"
    summary = (reports / "summary.md").read_text(encoding="utf-8")
    assert "1 additional records are in the full JSON report" in summary
    assert "… (see JSON report)" in summary
    assert "x" * 2000 not in summary
    assert json.loads((reports / "latest.json").read_text()) == report
