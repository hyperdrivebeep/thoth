from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_module_budget.py"


def _write_manifest(path: Path, *, baseline: int, symbols: list[str]) -> None:
    payload = {
        "schema_version": "1.0.0",
        "module_line_limit": 5,
        "function_line_limit": 3,
        "scan_roots": ["src/thoth"],
        "watched_modules": {
            "src/thoth/watched.py": {
                "baseline_lines": baseline,
                "top_level_symbols": symbols,
                "policy": "fixture",
            }
        },
        "watched_long_functions": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run(root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_current_repository_matches_module_responsibility_budget() -> None:
    result = _run(ROOT, ROOT / "config/module-responsibility-budget.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["verdict"] == "PASS"


def test_growth_of_watched_module_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\n\n\n\n# growth\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "watched module grew" in result.stdout


def test_new_oversized_module_fails(tmp_path: Path) -> None:
    watched = tmp_path / "src/thoth/watched.py"
    watched.parent.mkdir(parents=True)
    watched.write_text("def kept():\n    return 1\n\n\n\n", encoding="utf-8")
    (watched.parent / "new_big.py").write_text("\n".join(["value = 1"] * 6), encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "new oversized module" in result.stdout


def test_new_long_function_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "def kept():\n    value = 1\n    value += 1\n    return value\n\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "long-function ratchet changed" in result.stdout


def test_shrunk_module_requires_lower_ratchet_baseline(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=5, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "watched module shrank" in result.stdout


def test_new_top_level_responsibility_fails(tmp_path: Path) -> None:
    module = tmp_path / "src/thoth/watched.py"
    module.parent.mkdir(parents=True)
    module.write_text("def kept():\n    return 1\ndef added():\n    return 2\n", encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_manifest(manifest, baseline=4, symbols=["kept"])
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert "top-level responsibility changed" in result.stdout


def _write_web_manifest(
    path: Path,
    *,
    watched: dict[str, int] | None = None,
    excluded: list[str] | None = None,
) -> None:
    payload = {
        "schema_version": "1.0.0",
        "module_line_limit": 5,
        "function_line_limit": 3,
        "scan_roots": ["src/thoth"],
        "web_scan_roots": ["apps/web/src"],
        "web_excluded_suffixes": [".test.ts", ".test.tsx"] if excluded is None else excluded,
        "watched_modules": {
            relative: {"baseline_lines": baseline, "top_level_symbols": [], "policy": "fixture"}
            for relative, baseline in (watched or {}).items()
        },
        "watched_long_functions": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _web_file(root: Path, name: str, lines: int) -> Path:
    path = root / "apps/web/src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(["const value = 1;"] * lines) + "\n", encoding="utf-8")
    return path


def test_new_oversized_web_file_fails(tmp_path: Path) -> None:
    _web_file(tmp_path, "components/Big.tsx", 6)
    _web_file(tmp_path, "api/Small.ts", 5)
    manifest = tmp_path / "budget.json"
    _write_web_manifest(manifest)
    result = _run(tmp_path, manifest)
    assert result.returncode == 1
    assert (
        "new oversized module is not ratcheted: apps/web/src/components/Big.tsx=6" in result.stdout
    )
    assert "Small.ts" not in result.stdout


def test_watched_web_file_may_not_grow_and_must_lower_its_baseline_when_it_shrinks(
    tmp_path: Path,
) -> None:
    _web_file(tmp_path, "api/progress.ts", 7)
    manifest = tmp_path / "budget.json"
    _write_web_manifest(manifest, watched={"apps/web/src/api/progress.ts": 7})
    assert _run(tmp_path, manifest).returncode == 0
    _web_file(tmp_path, "api/progress.ts", 8)
    grown = _run(tmp_path, manifest)
    assert grown.returncode == 1 and "watched module grew" in grown.stdout
    _web_file(tmp_path, "api/progress.ts", 6)
    shrunk = _run(tmp_path, manifest)
    assert shrunk.returncode == 1 and "watched module shrank" in shrunk.stdout
    assert "update/refactor the ratchet" in shrunk.stdout


def test_web_test_files_are_not_measured_but_other_suffixes_are(tmp_path: Path) -> None:
    _web_file(tmp_path, "components/Big.test.tsx", 50)
    _web_file(tmp_path, "api/big.test.ts", 50)
    manifest = tmp_path / "budget.json"
    _write_web_manifest(manifest)
    assert _run(tmp_path, manifest).returncode == 0
    _web_file(tmp_path, "api/bigFixture.ts", 50)
    result = _run(tmp_path, manifest)
    assert result.returncode == 1 and "bigFixture.ts=50" in result.stdout
    _write_web_manifest(manifest, excluded=[])
    assert "Big.test.tsx" in _run(tmp_path, manifest).stdout


def test_watched_web_file_that_was_removed_is_reported(tmp_path: Path) -> None:
    manifest = tmp_path / "budget.json"
    _write_web_manifest(manifest, watched={"apps/web/src/api/gone.ts": 7})
    result = _run(tmp_path, manifest)
    assert result.returncode == 1 and "watched modules missing" in result.stdout


def test_a_web_file_is_measured_in_lines_not_by_python_syntax(tmp_path: Path) -> None:
    path = _web_file(tmp_path, "components/Odd.tsx", 5)
    path.write_text("export const a = <div>{{ not python }}</div>;\n" * 6, encoding="utf-8")
    manifest = tmp_path / "budget.json"
    _write_web_manifest(manifest)
    result = _run(tmp_path, manifest)
    assert result.returncode == 1 and "Odd.tsx=6" in result.stdout


def test_the_repository_limit_is_600_lines_and_the_function_limit_is_unchanged() -> None:
    manifest = json.loads((ROOT / "config/module-responsibility-budget.json").read_text("utf-8"))
    assert manifest["module_line_limit"] == 600
    assert manifest["function_line_limit"] == 200
    assert manifest["web_scan_roots"] == ["apps/web/src"]
    assert manifest["web_excluded_suffixes"] == [".test.ts", ".test.tsx"]


def test_every_oversized_repository_file_is_watched_with_its_exact_current_length() -> None:
    manifest = json.loads((ROOT / "config/module-responsibility-budget.json").read_text("utf-8"))
    limit = manifest["module_line_limit"]
    measured = {
        path.relative_to(ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for pattern, base in (
            ("*.py", "src/thoth"),
            ("*.ts", "apps/web/src"),
            ("*.tsx", "apps/web/src"),
        )
        for path in (ROOT / base).rglob(pattern)
        if not path.name.endswith((".test.ts", ".test.tsx"))
    }
    oversized = {name for name, count in measured.items() if count > limit}
    watched = manifest["watched_modules"]
    assert oversized <= set(watched), sorted(oversized - set(watched))
    for name, definition in watched.items():
        assert definition["baseline_lines"] == measured[name], name
