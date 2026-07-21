from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitRepository:
    def __init__(self, url: str, path: Path, branch: str = "main") -> None:
        self.url = url
        self.path = path
        self.branch = branch

    def _run(self, *arguments: str, capture: bool = False) -> str:
        command = ["git", "-C", str(self.path), *arguments]
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        return result.stdout.strip() if capture else ""

    def prepare(self, pull: bool) -> None:
        if not self.path.exists():
            if not pull:
                raise RuntimeError(f"Target repository is absent: {self.path}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    self.branch,
                    "--single-branch",
                    self.url,
                    str(self.path),
                ],
                check=True,
            )
        if not (self.path / ".git").exists():
            raise RuntimeError(f"Target is not a Git repository: {self.path}")
        remote = self._run("remote", "get-url", "origin", capture=True)
        if remote != self.url:
            raise RuntimeError(
                f"Unexpected origin for {self.path}: {remote!r}, expected {self.url!r}"
            )
        self._run("checkout", self.branch)
        if pull:
            self._run("pull", "--ff-only", "origin", self.branch)

    def commit(self, message: str, sign: bool = False) -> str | None:
        self._run("add", "--all", "--", "v1")
        changed = subprocess.run(
            ["git", "-C", str(self.path), "diff", "--cached", "--quiet"],
            check=False,
        ).returncode
        if changed == 0:
            return None
        arguments = ["commit"]
        if sign:
            arguments.append("-S")
        arguments.extend(["-m", message])
        self._run(*arguments)
        return self._run("rev-parse", "HEAD", capture=True)

    def push(self) -> None:
        self._run("push", "origin", f"HEAD:{self.branch}")


def sign_commits_from_environment() -> bool:
    return os.environ.get("STUDY_BUILDER_SIGN_COMMITS", "").casefold() in {"1", "true", "yes"}
