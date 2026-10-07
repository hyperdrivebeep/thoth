"""The synthetic radar demo folder is internally consistent and the checker catches edits."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
DEMO = REPO / "examples" / "synthetic-radar-demo-v1"
SCORER = REPO / "examples" / "synthetic-radar-demo-v1-scorer"
SCRIPT = REPO / "scripts" / "validate_synthetic_radar_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_synthetic_radar_demo", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load()


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A private copy of the demo and scorer folders to damage."""
    root = tmp_path / "synthetic-radar-demo-v1"
    shutil.copytree(DEMO, root)
    shutil.copytree(SCORER, tmp_path / "synthetic-radar-demo-v1-scorer")
    return root


def _rewrite_manifest(root: Path) -> None:
    (root / validator.MANIFEST_NAME).write_text(
        validator.manifest_text(validator.file_digests(root)), encoding="utf-8", newline="\n"
    )


def test_shipped_demo_is_consistent() -> None:
    assert validator.validate(DEMO) == []


def test_rates_are_recomputed_from_the_trial_files() -> None:
    result = validator.read_yaml(DEMO / "31_RESULT_RAIN_SYNTHETIC.yaml")
    rows, problems = validator.read_trials(DEMO / result["trials_csv"])
    assert problems == []
    detected = sum(
        1 for row in rows if row["row_kind"] == "TARGET_TRIAL" and row["detected"] == "1"
    )
    assert (detected, str(validator.rate(detected, 20))) == (16, "0.80")
    events = sum(1 for row in rows if row["row_kind"] == "FALSE_TRACK_EVENT")
    assert (events, str(validator.rate(events, 10))) == (7, "0.70")


def test_edited_trial_row_is_reported_even_when_the_manifest_is_refreshed(copy: Path) -> None:
    path = copy / "data" / "31_RAIN_TRIALS_SYNTHETIC.csv"
    text = path.read_text(encoding="utf-8").replace("SYN-T-RAIN-17,,0,", "SYN-T-RAIN-17,,1,")
    path.write_text(text, encoding="utf-8", newline="\n")
    _rewrite_manifest(copy)
    problems = validator.validate(copy)
    assert any("detection recomputed" in problem for problem in problems)


def test_edit_without_refreshing_the_manifest_is_reported(copy: Path) -> None:
    path = copy / "20_TEST_PLAN_SYNTHETIC.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Dry weather run", "Dry run"),
        encoding="utf-8",
        newline="\n",
    )
    assert any("changed since the manifest" in problem for problem in validator.validate(copy))


def test_duplicate_id_is_reported(copy: Path) -> None:
    path = copy / "20_TEST_PLAN_SYNTHETIC.yaml"
    text = path.read_text(encoding="utf-8").replace("id: SYN-TC-RAIN", "id: SYN-TC-DRY")
    path.write_text(text, encoding="utf-8", newline="\n")
    _rewrite_manifest(copy)
    assert any("duplicate id SYN-TC-DRY" in problem for problem in validator.validate(copy))


def test_missing_notice_is_reported(copy: Path) -> None:
    path = copy / "01_SOURCE_LINEAGE.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[1:]), encoding="utf-8", newline="\n")
    _rewrite_manifest(copy)
    assert any("not the synthetic notice" in problem for problem in validator.validate(copy))


def test_unit_mismatch_between_criterion_and_result_is_reported(copy: Path) -> None:
    path = copy / "10_REQUIREMENTS_SYNTHETIC.yaml"
    text = path.read_text(encoding="utf-8").replace(
        'threshold: "0.90", unit: ratio, condition: weather=rain',
        'threshold: "0.90", unit: per_min, condition: weather=rain',
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    _rewrite_manifest(copy)
    assert any("differs from the criterion's" in problem for problem in validator.validate(copy))


def test_schema_rejects_a_missing_required_field(copy: Path) -> None:
    path = copy / "10_REQUIREMENTS_SYNTHETIC.yaml"
    text = path.read_text(encoding="utf-8").replace(", required: true}", "}", 1)
    path.write_text(text, encoding="utf-8", newline="\n")
    _rewrite_manifest(copy)
    assert any("missing 'required'" in problem for problem in validator.validate(copy))


def test_scorer_naming_an_unknown_criterion_is_reported(copy: Path) -> None:
    scorer = copy.parent / "synthetic-radar-demo-v1-scorer" / "40_ASSESSMENT_DRAFT_SYNTHETIC.yaml"
    scorer.write_text(
        scorer.read_text(encoding="utf-8").replace("SYN-C-DET-FOG", "SYN-C-DET-SNOW", 1),
        encoding="utf-8",
        newline="\n",
    )
    assert any(
        "unknown criterion SYN-C-DET-SNOW" in problem for problem in validator.validate(copy)
    )


def test_manifest_says_it_is_only_an_integrity_check() -> None:
    manifest = validator.read_yaml(DEMO / validator.MANIFEST_NAME)
    statement = manifest["integrity_statement"]
    assert "do not show that the data is true" in statement and "approved" in statement
