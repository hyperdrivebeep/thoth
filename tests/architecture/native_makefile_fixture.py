"""Host-native paths for disposable Makefile stage fixtures; no stage assertions change."""

import os
import sys
from pathlib import Path


def native_makefile_source(source: str, root: Path) -> str:
    source = source.replace(
        "$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path",
        "$RepoRoot = '" + str(root).replace("'", "''") + "'",
    )
    if os.name != "nt":
        source = source.replace(
            '$Python = Join-Path $RepoRoot ".venv\\Scripts\\python.exe"',
            "$Python = '" + sys.executable.replace("'", "''") + "'",
        )
        # These fixtures explicitly stub all Web work; the unused pnpm slot is
        # independent of the real frontend verification run.
        source = source.replace("$Pnpm = $env:THOTH_PNPM", "$Pnpm = $Python")
    return source


def powershell_executable() -> str:
    return "pwsh.exe" if os.name == "nt" else "pwsh"
