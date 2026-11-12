"""CI and the local lint task refuse unformatted Python on the paths that are linted."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATHS = "src apps tests migrations"


def test_checks_job_refuses_unformatted_python_on_the_lint_paths() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/product-check.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    steps = workflow["jobs"]["checks"]["steps"]
    lint = next(step for step in steps if step.get("name") == "Python lint")
    formatted = next(step for step in steps if step.get("name") == "Python format")
    assert lint["run"].endswith(f"-m ruff check {PATHS}")
    assert formatted["run"].endswith(f"-m ruff format --check {PATHS}")
    assert formatted["if"] == lint["if"]
    assert "continue-on-error" not in formatted


def test_local_lint_task_checks_the_format_of_the_same_paths() -> None:
    text = (ROOT / "Makefile.ps1").read_text(encoding="utf-8")
    paths = ", ".join(f'"{part}"' for part in PATHS.split())
    assert f'@("-m", "ruff", "check", {paths})' in text
    assert f'@("-m", "ruff", "format", "--check", {paths})' in text
