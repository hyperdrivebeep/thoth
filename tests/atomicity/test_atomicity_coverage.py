import json
from pathlib import Path

import pytest
from scripts.check_atomicity_coverage import OWNERS, coverage_errors, passed_nodes, source_manifest

ROOT = Path(__file__).resolve().parents[2]


def workspace(tmp_path: Path) -> None:
    for name in (
        "config/architecture-conformance.json",
        "config/atomicity-requirements.json",
        "config/atomicity-paths.json",
        "schemas/protocol/public-method-catalog.json",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())


def test_removing_debt_fields_cannot_create_verified_atomicity(tmp_path: Path) -> None:
    workspace(tmp_path)
    path = tmp_path / "config/architecture-conformance.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for owner in manifest["canonical_owners"]:
        if owner["aggregate"] in OWNERS:
            owner.pop("atomicity_debt", None)
            owner["current_atomicity"] = "ATOMIC"
    requirement_path = tmp_path / "config/atomicity-requirements.json"
    requirements = json.loads(requirement_path.read_text(encoding="utf-8"))
    requirements.pop("evidence_file", None)
    requirement_path.write_text(json.dumps(requirements), encoding="utf-8")
    errors = coverage_errors(tmp_path, manifest, require_complete=True)
    assert any("lacks source-bound execution evidence" in error for error in errors)


def test_query_and_control_inventory_cannot_be_deleted_to_hide_writers(tmp_path: Path) -> None:
    workspace(tmp_path)
    path = tmp_path / "config/atomicity-paths.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["public_entries"] = [
        row for row in data["public_entries"] if row["name"] != "receipt/audit/read"
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any("omits or duplicates" in error for error in coverage_errors(tmp_path))


@pytest.mark.parametrize("result", ["failure", "error", "skipped"])
def test_nonpassing_execution_is_never_closure_evidence(tmp_path: Path, result: str) -> None:
    path = tmp_path / "evidence.xml"
    path.write_text(
        '<testsuites><testsuite tests="1">'
        '<testcase classname="tests.atomicity.test_example" name="test_case">'
        f"<{result}/></testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="did not pass"):
        passed_nodes(path)


def test_new_backend_writer_invalidates_source_fingerprint(tmp_path: Path) -> None:
    workspace(tmp_path)
    before = source_manifest(tmp_path)
    source = tmp_path / "src/thoth/extra_writer.py"
    source.parent.mkdir(parents=True)
    source.write_text("def newly_registered_writer():\n    return None\n", encoding="utf-8")
    after = source_manifest(tmp_path)
    assert after != before
    assert any(row["path"] == "src/thoth/extra_writer.py" for row in after)


def test_phase_binding_must_exist_even_when_owner_is_still_open(tmp_path: Path) -> None:
    workspace(tmp_path)
    path = tmp_path / "config/atomicity-paths.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["public_entries"][0]["phase_ids"] = ["invented:phase"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any("phase binding missing" in error for error in coverage_errors(tmp_path))


def test_partial_owner_closure_cannot_omit_shared_route_obligations(tmp_path: Path) -> None:
    workspace(tmp_path)
    path = tmp_path / "config/architecture-conformance.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    requirements = json.loads((tmp_path / "config/atomicity-requirements.json").read_text())
    historical = {row["owner"]: row["historical_debt"] for row in requirements["owners"]}
    # Construct the partial-closure counterexample independently of the live repo's
    # OPEN/closed state, so this guard stays meaningful after successful closure.
    for row in manifest["canonical_owners"]:
        if row["aggregate"] in OWNERS:
            row["current_atomicity"] = "PARTIAL"
            row["atomicity_debt"] = historical[row["aggregate"]]
            row.pop("atomicity_evidence", None)
    owner = next(
        row for row in manifest["canonical_owners"] if row["aggregate"] == "PROJECT_GOVERNANCE"
    )
    owner["current_atomicity"] = "ATOMIC"
    owner.pop("atomicity_debt", None)
    errors = coverage_errors(tmp_path, manifest)
    assert any("partial owner closure is unsupported" in error for error in errors)
