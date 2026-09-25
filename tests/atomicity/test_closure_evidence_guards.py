"""Counterexamples from the independent UOW13 review; all artifacts are temporary fixtures."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.atomicity_discovery_evidence import discovery_errors, support_sources
from scripts.atomicity_invocation import command, environment, identity, receipt_errors
from scripts.atomicity_phase_evidence import phase_errors

NODE = "tests/atomicity/test_fixture.py::test_fault_and_reopen"
ENTRY = "fixture.publish"
WRITER = "fixture.write"


def phase(root: Path) -> dict[str, Any]:
    source = "def test_fault_and_reopen():\n    assert current_head == old_head"
    path = root / NODE.split("::")[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return {
        "path_id": "fixture:publication",
        "actual_callable": ENTRY,
        "producer_refs": [WRITER],
        "disposition": "VERIFIED",
        "evidence_refs": [NODE],
        "verification": {
            "classification": "MUTATION",
            "required_callables": [ENTRY, WRITER],
            "cases": [
                {
                    "nodeid": NODE,
                    "scenario": "Injected failure preserves old head on reopen",
                    "observed_callables": [ENTRY, WRITER],
                    "test_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                }
            ],
        },
    }


def test_each_required_phase_needs_its_own_verified_scenario(tmp_path: Path) -> None:
    complete = phase(tmp_path)
    pending = copy.deepcopy(complete)
    pending["path_id"] = "fixture:second-publication"
    pending["disposition"] = "OWNER_EVIDENCE_PENDING"
    pending["evidence_refs"] = []
    observations = {NODE: [ENTRY, WRITER]}
    assert phase_errors(tmp_path, complete, {NODE}, observations) == []
    assert phase_errors(tmp_path, pending, {NODE}, observations)


def test_a_passing_file_sibling_cannot_replace_the_required_fault_case(tmp_path: Path) -> None:
    sibling = NODE.replace("fault_and_reopen", "happy_path")
    errors = phase_errors(tmp_path, phase(tmp_path), {sibling}, {sibling: [ENTRY, WRITER]})
    assert any("scenario did not pass" in error for error in errors)


def test_mentioned_but_unexecuted_writer_is_not_evidence(tmp_path: Path) -> None:
    errors = phase_errors(tmp_path, phase(tmp_path), {NODE}, {NODE: [ENTRY]})
    assert any("mandatory entry/writer" in error for error in errors)


def test_unresolved_phase_blocker_is_not_a_successful_disposition(tmp_path: Path) -> None:
    row = phase(tmp_path)
    row["blockers"] = ["current revision competition is not exercised"]
    assert phase_errors(tmp_path, row, {NODE}, {NODE: [ENTRY, WRITER]})


def test_shell_pytest_filters_and_plugins_do_not_enter_owner_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k happy_path")
    monkeypatch.setenv("PYTEST_PLUGINS", "undeclared_plugin")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "parent test")
    env = environment(tmp_path)
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    assert "PYTEST_CURRENT_TEST" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def receipt(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    required = {
        "owners": [{"test_files": [NODE.split("::")[0]]}],
        "common_tests": [],
        "required_nodes": [NODE],
    }
    invocation = identity(tmp_path)
    base = command(tmp_path, required, invocation["interpreter"])
    result = {
        "exit_code": 0,
        "source_unchanged": True,
        "collected": 1,
        "invocation": invocation,
        "command": base,
        "collection_command": [*base, "--collect-only"],
        "junit_file": "run/junit.xml",
        "observations_file": "run/observations.json",
        "execution_command": [
            *base,
            "-vv",
            f"--junitxml={tmp_path / 'run/junit.xml'}",
            f"--atomicity-observations={tmp_path / 'run/observations.json'}",
        ],
    }
    return required, result


@pytest.mark.parametrize(
    "field,value",
    [
        ("exit_code", 1),
        ("exit_code", False),
        ("source_unchanged", False),
        ("collected", 2),
        ("command", ["pytest", "-k", "happy"]),
        ("execution_command", ["pytest"]),
        ("invocation", {}),
    ],
)
def test_reader_independently_rejects_invalid_execution_receipt(
    tmp_path: Path, field: str, value: object
) -> None:
    required, result = receipt(tmp_path)
    assert receipt_errors(tmp_path, required, result, [NODE]) == []
    result[field] = value
    assert receipt_errors(tmp_path, required, result, [NODE])


def test_matching_but_narrowed_collection_cannot_replace_required_nodes(tmp_path: Path) -> None:
    required, result = receipt(tmp_path)
    required["required_nodes"].append(NODE + "_second")
    assert any(
        "required node contract differs" in error
        for error in receipt_errors(tmp_path, required, result, [NODE])
    )


def test_new_source_with_old_reviewed_internal_writer_inventory_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src/thoth/application/background.py"
    source.parent.mkdir(parents=True)
    source.write_text("def prepare():\n    pass\n", encoding="utf-8")
    discovery: dict[str, Any] = {
        "files": [
            {
                "path": source.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "runtime_bindings": [],
        "configuration": {
            "runtime_bindings": True,
            "call_graph": True,
            "configured": True,
            "projectpack_probe": True,
        },
        "projectpack_probe": {"observed_callable_count": 1, "call_graph": {}},
    }
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(discovery), encoding="utf-8")
    scanner_files: list[dict[str, str]] = []
    for name in ("build_atomicity_path_inventory.py", "atomicity_call_graph.py"):
        scanner = tmp_path / "scripts" / name
        scanner.parent.mkdir(exist_ok=True)
        scanner.write_text("# fixture scanner\n", encoding="utf-8")
        scanner_files.append(
            {"path": f"scripts/{name}", "sha256": hashlib.sha256(scanner.read_bytes()).hexdigest()}
        )
    inventory: dict[str, Any] = {
        "discovery": "discovery.json",
        "public_entries": [],
        "phases": [],
        "discovery_binding": {
            "support_sources": support_sources(tmp_path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scanner_files": scanner_files,
            "unresolved_reviews": [],
        },
    }
    assert discovery_errors(tmp_path, inventory) == []
    source.write_text(
        "def prepare():\n    database.insert('new_background_writer')\n", encoding="utf-8"
    )
    assert any(
        "stale for current internal/background writer" in error
        for error in discovery_errors(tmp_path, inventory)
    )
