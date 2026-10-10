"""The demo preparation script reads its file names and result parts from the demo manifest.

A demo folder without those manifest entries is the radar demo as it always was: the CSV the script
writes for it must stay byte for byte the same.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).parents[2]
RADAR = REPO / "examples" / "synthetic-radar-demo-v1"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "prepare_demo_spec", REPO / "scripts" / "prepare_synthetic_radar_demo.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare = _load()

SPANS = {
    "30_RESULT_DRY_SYNTHETIC.yaml": {"detection": "span:d1", "false_track": "span:f1"},
    "31_RESULT_RAIN_SYNTHETIC.yaml": {"detection": "span:d2", "false_track": "span:f2"},
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_radar_demo_csv_files_are_byte_for_byte_what_they_were() -> None:
    first = prepare.phase1_csv(RADAR, {k: v for k, v in SPANS.items() if "DRY" in k})
    second = prepare.phase2_csv(RADAR, SPANS, "a" * 64)
    # taken from the script before it read anything from the manifest
    assert (len(first), _digest(first)) == (
        9083,
        "3de8cd82e43a206848d412293f5c14a57bc8189b4bbc2900e4fff1d93c987538",
    )
    assert (len(second), _digest(second)) == (
        1970,
        "05a5a983c7bcf7193a2abb6de5846515f14337fb7f78188b8dfe7971d19a98fa",
    )


def _demo(root: Path) -> Path:
    """A small demo in another field whose manifest names its own files and result parts."""
    (root / "00_MANIFEST_SYNTHETIC.yaml").write_text(
        "result_parts: [capacity, resistance]\n"
        "requirement_file: 10_REQ.yaml\n"
        "test_plan_file: 20_PLAN.yaml\n"
        "version_label: synthetic-other-demo-v1\n",
        encoding="utf-8",
    )
    (root / "10_REQ.yaml").write_text(
        "requirement:\n"
        "  id: SYN-REQ-X-001\n"
        "  title: Example requirement\n"
        "  criteria:\n"
        "    - {id: SYN-C-CAP-A, title: Capacity, measure: capacity, comparator: '>=',"
        " threshold: '0.80', unit: ratio, condition: temp=a, required: true}\n"
        "    - {id: SYN-C-RES-A, title: Resistance, measure: resistance, comparator: '<=',"
        " threshold: '0.30', unit: ratio, condition: temp=a, required: true}\n",
        encoding="utf-8",
    )
    (root / "20_PLAN.yaml").write_text(
        "test_cases:\n"
        "  - {id: SYN-TC-A, title: Run A, condition: temp=a, covers: [SYN-C-CAP-A, SYN-C-RES-A]}\n",
        encoding="utf-8",
    )
    (root / "30_RESULT_A.yaml").write_text(
        "available_from_phase: 1\n"
        "condition: temp=a\n"
        "test_case: SYN-TC-A\n"
        "observed_at: '2026-01-01T00:00:00Z'\n"
        "capacity:\n"
        "  criterion: SYN-C-CAP-A\n"
        "  unit: ratio\n"
        "  value: '0.95'\n"
        "resistance:\n"
        "  criterion: SYN-C-RES-A\n"
        "  numerator: 3\n"
        "  denominator: 10\n"
        "  unit: ratio\n"
        "  value: '0.30'\n",
        encoding="utf-8",
    )
    return root


def test_a_demo_manifest_names_the_files_and_the_result_parts(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    spec = prepare.demo_spec(demo)
    assert spec.result_parts == ("capacity", "resistance")
    assert (spec.requirement_file, spec.test_plan_file) == ("10_REQ.yaml", "20_PLAN.yaml")
    assert spec.version_label == "synthetic-other-demo-v1"
    spans = {"30_RESULT_A.yaml": {"capacity": "span:c", "resistance": "span:r"}}
    trace = prepare.build_trace_set(demo, 1, spans)
    by_criterion = {result.criterion_id: result for result in trace.results}
    assert set(by_criterion) == {"SYN-C-CAP-A", "SYN-C-RES-A"}
    capacity, resistance = by_criterion["SYN-C-CAP-A"], by_criterion["SYN-C-RES-A"]
    assert (capacity.numerator, capacity.denominator) == (None, None)  # a mean has no counts
    assert (resistance.numerator, resistance.denominator) == (3, 10)
    assert capacity.source_span_refs == ("span:c",) and resistance.source_span_refs == ("span:r",)


def test_the_value_lines_follow_the_result_parts() -> None:
    text = (
        "capacity:\n  criterion: X\n  value: '1'\nresistance:\n  value: '2'\n"
        "detection:\n  value: '3'\n"
    )
    both = prepare.value_lines(text, ("capacity", "resistance"))
    assert both == {"capacity": 3, "resistance": 5}
    assert prepare.value_lines(text) == {"detection": 7}  # the radar parts when none are given


def test_a_folder_without_manifest_entries_is_read_as_the_radar_demo() -> None:
    spec = prepare.demo_spec(RADAR)
    assert spec.result_parts == ("detection", "false_track")
    assert spec.requirement_file == "10_REQUIREMENTS_SYNTHETIC.yaml"
    assert spec.test_plan_file == "20_TEST_PLAN_SYNTHETIC.yaml"
    assert spec.version_label == "synthetic-radar-demo-v1"
