from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaseInput:
    label: str
    result_path: Path
    workspace: Path


def _case(value: str) -> CaseInput:
    parts = value.split("|", maxsplit=2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("case must be LABEL|RESULT_JSON|WORKSPACE")
    return CaseInput(parts[0], Path(parts[1]), Path(parts[2]))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    selected = [root / "src", root / "schemas", root / "pyproject.toml", root / "uv.lock"]
    files: list[Path] = []
    for entry in selected:
        if entry.is_file():
            files.append(entry)
        elif entry.is_dir():
            files.extend(path for path in entry.rglob("*") if path.is_file())
    for path in sorted(files, key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _core_token_hits(root: Path, forbidden_tokens: tuple[str, ...]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {token: [] for token in forbidden_tokens}
    for path in sorted((root / "src" / "thoth").rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            if token.lower() in text:
                hits[token].append(path.relative_to(root).as_posix())
    return hits


def _db_rows(workspace: Path, query: str) -> list[tuple[Any, ...]]:
    database = workspace / "db" / "thoth.sqlite3"
    with sqlite3.connect(database) as connection:
        return list(connection.execute(query))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", required=True, type=_case)
    parser.add_argument("--forbidden-token", action="append", default=[])
    parser.add_argument("--forbidden-runtime-text", action="append", default=[])
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    arguments = parser.parse_args()

    root = Path.cwd().resolve()
    cases: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    project_ids: list[str] = []

    def record(name: str, passed: bool, detail: Any) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    for item in arguments.case:
        result = _read_json(item.result_path)
        cycle = result["cycle"]
        project_id = result["project"]["project_id"]
        project_ids.append(project_id)
        actions = cycle["action_plan"]["alternatives"]
        action_by_id = {action["action_id"]: action for action in actions}
        frontier = cycle["action_plan"]["frontier"]
        frontier_tiers = [action_by_id[action_id]["risk_tier"] for action_id in frontier]
        evidence_text = "\n".join(span["exact_text"] for span in result["evidence"])
        memory_projects = _db_rows(
            item.workspace, "select distinct project_id from memory_records order by project_id"
        )
        receipt_rows = _db_rows(
            item.workspace,
            "select project_id, receipt_digest from receipts order by created_at",
        )
        receipt = cycle["commit"]["receipt"]
        case_summary = {
            "label": item.label,
            "pack_id": result["pack_id"],
            "project_id": project_id,
            "evidence_count": result["evidence_count"],
            "selected_evidence_count": result["selected_evidence_count"],
            "statuses": cycle["assessment"]["derived_status"],
            "hypothesis_count": len(cycle["portfolio"]["hypotheses"]),
            "action_count": len(actions),
            "frontier_tiers": frontier_tiers,
            "revision_count": len(cycle["commit"]["committed_revision_ids"]),
            "receipt_digest": receipt["receipt_digest"],
            "semantic_repair_attempted": cycle.get("semantic_repair_attempted", False),
        }
        cases.append(case_summary)
        record(
            f"{item.label}:action_compiler_ready",
            cycle["action_compilation"]["status"] == "READY",
            cycle["action_compilation"]["status"],
        )
        record(
            f"{item.label}:multiple_hypotheses",
            len(cycle["portfolio"]["hypotheses"]) >= 3,
            len(cycle["portfolio"]["hypotheses"]),
        )
        record(
            f"{item.label}:multiple_action_families",
            len({action["action_family"] for action in actions}) >= 2,
            sorted({action["action_family"] for action in actions}),
        )
        record(
            f"{item.label}:no_R3_R4_in_frontier",
            not any(tier in {"R3", "R4"} for tier in frontier_tiers),
            frontier_tiers,
        )
        record(
            f"{item.label}:receipt_not_truth_certificate",
            receipt["semantic_truth_certified"] is False,
            receipt["semantic_truth_certified"],
        )
        record(
            f"{item.label}:receipt_db_readback",
            receipt_rows == [(project_id, receipt["receipt_digest"])],
            receipt_rows,
        )
        record(
            f"{item.label}:memory_project_isolated",
            memory_projects == [(project_id,)],
            memory_projects,
        )
        record(
            f"{item.label}:committed_revisions",
            len(cycle["commit"]["committed_revision_ids"]) >= 3,
            len(cycle["commit"]["committed_revision_ids"]),
        )
        record(
            f"{item.label}:selected_evidence_bounded",
            0 < result["selected_evidence_count"] <= min(result["evidence_count"], 160),
            [result["selected_evidence_count"], result["evidence_count"]],
        )
        for marker in arguments.forbidden_runtime_text:
            record(
                f"{item.label}:runtime_excludes:{marker}",
                marker.lower() not in evidence_text.lower(),
                marker,
            )

    record("project_ids_unique", len(project_ids) == len(set(project_ids)), project_ids)
    token_hits = _core_token_hits(root, tuple(arguments.forbidden_token))
    for token, hits in token_hits.items():
        record(f"core_excludes_project_token:{token}", not hits, hits)

    payload = {
        "verdict": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "runtime_bundle_digest": _tree_digest(root),
        "case_count": len(cases),
        "cases": cases,
        "checks": checks,
    }
    arguments.json_out.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    arguments.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# THOTH Four-ProjectPack Portability Audit",
        "",
        f"Verdict: **{payload['verdict']}**",
        "",
        f"Runtime bundle digest: `{payload['runtime_bundle_digest']}`",
        "",
        "| Pack | Project | Evidence | Status | Hypotheses | Actions | Revisions |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for case in cases:
        lines.append(
            f"| {case['label']} | `{case['project_id']}` | "
            f"{case['selected_evidence_count']}/{case['evidence_count']} | "
            f"{', '.join(case['statuses'])} | {case['hypothesis_count']} | "
            f"{case['action_count']} | {case['revision_count']} |"
        )
    lines.extend(["", "## Checks", ""])
    for check in checks:
        lines.append(
            f"- {'PASS' if check['passed'] else 'FAIL'} — `{check['name']}`: `{check['detail']}`"
        )
    arguments.markdown_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "checks": len(checks),
                "runtime_bundle_digest": payload["runtime_bundle_digest"],
            }
        )
    )
    if payload["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
