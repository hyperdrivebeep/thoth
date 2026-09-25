"""Run the declared owner regressions once and record exact source/collection/result identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from atomicity_discovery_evidence import discovery_errors
from atomicity_invocation import command as pytest_command
from atomicity_invocation import environment, identity, receipt_errors
from atomicity_phase_evidence import phase_errors
from check_atomicity_coverage import (
    ROOT,
    contract_digest,
    digest,
    load,
    passed_nodes,
    source_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    args = parser.parse_args()
    if not args.run_name.replace("-", "").isalnum():
        raise ValueError("invalid atomicity run name")
    requirements = load(ROOT, "config/atomicity-requirements.json")
    inventory = load(ROOT, "config/atomicity-paths.json")
    if errors := discovery_errors(ROOT, inventory):
        raise ValueError("; ".join(errors))
    directory = ROOT / "outputs/owner-atomicity-20260914" / args.run_name
    directory.mkdir(parents=True, exist_ok=False)
    before = source_manifest(ROOT)
    (directory / "source.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    invocation = identity(ROOT)
    command = pytest_command(ROOT, requirements, str(Path(sys.executable).resolve()))
    env = environment(ROOT)
    collection_command = [*command, "--collect-only"]
    with (directory / "collection.txt").open("w", encoding="utf-8") as output:
        collected = subprocess.run(
            collection_command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT
        )
    if collected.returncode:
        return collected.returncode
    nodes = sorted(
        line.strip()
        for line in (directory / "collection.txt").read_text(encoding="utf-8").splitlines()
        if line.startswith("tests/") and "::" in line
    )
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("owner collection is empty or duplicated")
    if requirements.get("required_nodes") != nodes:
        raise ValueError("collection differs from the predeclared required node contract")
    (directory / "collection.json").write_text(json.dumps(nodes, indent=2), encoding="utf-8")
    xml = directory / "junit.xml"
    observations = directory / "observations.json"
    execution_command = [
        *command,
        "-vv",
        f"--junitxml={xml}",
        f"--atomicity-observations={observations}",
    ]
    with (directory / "pytest.txt").open("w", encoding="utf-8") as output:
        completed = subprocess.run(
            execution_command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT
        )
    record = {
        "at": datetime.now(UTC).isoformat(),
        "command": command,
        "collection_command": collection_command,
        "execution_command": execution_command,
        "invocation": invocation,
        "exit_code": completed.returncode,
        "source_unchanged": source_manifest(ROOT) == before,
        "collected": len(nodes),
        "junit_file": xml.relative_to(ROOT).as_posix(),
        "observations_file": observations.relative_to(ROOT).as_posix(),
    }
    (directory / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if completed.returncode or not record["source_unchanged"]:
        print(json.dumps(record))
        return completed.returncode or 1
    if set(nodes) != passed_nodes(xml):
        raise ValueError("owner collection/result identity mismatch")
    if errors := receipt_errors(ROOT, requirements, record, nodes):
        raise ValueError("; ".join(errors))
    observed = load(ROOT, observations.relative_to(ROOT).as_posix())
    phase_failures = []
    tracked = {row["owner"] for row in requirements["owners"]}
    for phase in inventory["phases"]:
        if phase["owner"] not in tracked:
            continue
        # Validate prospective closure without mutating the OPEN development ledger.
        candidate = dict(phase)
        if candidate.get("verification", {}).get("classification") == "MUTATION":
            candidate["disposition"] = "VERIFIED"
            candidate["evidence_refs"] = [c["nodeid"] for c in candidate["verification"]["cases"]]
        phase_failures.extend(phase_errors(ROOT, candidate, set(nodes), observed["nodes"]))
    (directory / "phase-errors.json").write_text(
        json.dumps(phase_failures, indent=2), encoding="utf-8"
    )
    if phase_failures:
        raise ValueError(
            "Owner scenarios passed but required path observations are incomplete; "
            "see phase-errors.json"
        )
    if contract_digest(requirements, inventory) != contract_digest(
        load(ROOT, "config/atomicity-requirements.json"), load(ROOT, "config/atomicity-paths.json")
    ):
        raise ValueError("owner path contract changed during verification")
    evidence = {
        **record,
        "source_files": before,
        "source_digest": digest(before),
        "contract_digest": contract_digest(requirements, inventory),
        "junit_file": xml.relative_to(ROOT).as_posix(),
        "junit_sha256": hashlib.sha256(xml.read_bytes()).hexdigest(),
        "collection_file": (directory / "collection.json").relative_to(ROOT).as_posix(),
        "collection_digest": digest(nodes),
        "observations_sha256": hashlib.sha256(observations.read_bytes()).hexdigest(),
        "result_file": (directory / "result.json").relative_to(ROOT).as_posix(),
        "result_sha256": hashlib.sha256((directory / "result.json").read_bytes()).hexdigest(),
        "scope": "Focused declared owner regression evidence; not FULL or field/live evidence",
    }
    destination = ROOT / requirements["evidence_file"]
    with destination.open("x", encoding="utf-8") as output:
        json.dump(evidence, output, indent=2)
    print(json.dumps({k: v for k, v in record.items() if k != "command"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
