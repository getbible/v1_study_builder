# SPDX-License-Identifier: GPL-2.0-only
import subprocess
from pathlib import Path

import pytest

from study_builder.git import GitRepository


@pytest.fixture
def output_repository(tmp_path: Path) -> GitRepository:
    subprocess.run(["git", "init", "--initial-branch=main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "https://example.com/output.git"],
        check=True,
    )
    (tmp_path / "README.md").write_text("Output repository\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test Author",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Initialize output repository",
        ],
        check=True,
    )
    return GitRepository("https://example.com/output.git", tmp_path)


@pytest.mark.parametrize("state", ["staged", "unstaged", "untracked"])
def test_prepare_refuses_dirty_output_repository(output_repository, state: str) -> None:
    path = output_repository.path
    changed = path / ("untracked.txt" if state == "untracked" else "README.md")
    changed.write_text("Preserve local changes\n", encoding="utf-8")
    if state == "staged":
        subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        output_repository.prepare(pull=False)

    assert changed.read_text(encoding="utf-8") == "Preserve local changes\n"


def test_prepare_accepts_clean_output_repository(output_repository) -> None:
    output_repository.prepare(pull=False)
