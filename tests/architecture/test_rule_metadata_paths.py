from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from scripts.rule_transaction_contract import safe_metadata


def test_traversal_and_windows_reparse_metadata_paths_are_rejected(tmp_path: Path) -> None:
    root, backing = tmp_path / "root", tmp_path / "backing"
    root.mkdir()
    backing.mkdir()
    assert root.resolve().is_relative_to(tmp_path.resolve())
    assert backing.resolve().is_relative_to(tmp_path.resolve())
    target = root / "config"
    if os.name == "nt":
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(backing)],
            capture_output=True, check=False,
        )
        assert linked.returncode == 0, linked.stderr.decode(errors="replace")
    else:
        target.symlink_to(backing, target_is_directory=True)
    (backing / "architecture-conformance.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="exact allowed"):
        safe_metadata(root, "config/architecture-conformance.json")
    with pytest.raises(ValueError, match="exact allowed"):
        safe_metadata(root, "config/../backing/architecture-conformance.json")
