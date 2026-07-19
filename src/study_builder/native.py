# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from study_builder.contract import GetBibleSwordContractReader
from study_builder.models import NativeExport


class SwordExporter:
    def __init__(self, executable: Path, schema_path: Path, contract: str) -> None:
        self.executable = executable
        self.reader = GetBibleSwordContractReader(schema_path, contract)

    def export(self, module_root: Path, module_name: str) -> NativeExport:
        if not self.executable.is_file():
            raise FileNotFoundError(f"getbiblesword executable not found: {self.executable}")
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [
                    str(self.executable.resolve()),
                    "extract",
                    "--sword-path",
                    str(module_root.resolve()),
                    "--module",
                    module_name,
                ],
                stdout=subprocess.PIPE,
                stderr=errors,
            )
            assert process.stdout is not None
            try:
                exported = self.reader.read(process.stdout)
                return_code = process.wait()
            except Exception as error:
                if process.poll() is None:
                    process.kill()
                process.wait()
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()
                suffix = f"; stderr: {detail}" if detail else ""
                raise RuntimeError(
                    f"getbiblesword validation failed for {module_name}: {error}{suffix}"
                ) from error
            finally:
                process.stdout.close()
            if return_code:
                exported.close()
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"getbiblesword failed for {module_name} with status {return_code}: {detail}"
                )
        return exported
