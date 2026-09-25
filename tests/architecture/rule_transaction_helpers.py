from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.verification_identity import reviewed_paths

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def rule_repository() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory(prefix="thoth-tx-") as temporary:
        root = Path(temporary)
        cloned = subprocess.run(
            ["git", "clone", "--local", "--shared", "--no-checkout", str(ROOT), str(root)],
            capture_output=True, check=False,
        )
        assert cloned.returncode == 0, cloned.stderr.decode()
        for relative in reviewed_paths(ROOT):
            source = ROOT / relative
            if source.is_file():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        yield root


def run_cli(root: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, f"scripts/{script}.py", *args], cwd=root,
        env={**os.environ, "THOTH_GATE_DIR": str(root / ".thoth/architecture")},
        capture_output=True, encoding="utf-8", check=False, timeout=90,
    )


def prepare(root: Path) -> dict[str, Any]:
    result = run_cli(
        root, "prepare_architecture_preflight", "--acceptance", "A11",
        "--scope", "config", "--scope", "src/thoth/domain",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
