from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, cast


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    arguments = parser.parse_args()
    database = arguments.workspace / "db" / "thoth.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        project_id = arguments.project_id
        if project_id is None:
            row = connection.execute(
                "select project_id from projects where project_id like 'project:live-qa-%' "
                "order by rowid desc limit 1"
            ).fetchone()
            if row is None:
                raise SystemExit("live QA project was not found")
            project_id = str(row["project_id"])
        operation_rows = connection.execute(
            "select method, state, count(*) as count from operations where project_id=? "
            "group by method, state order by method, state",
            (project_id,),
        ).fetchall()
        operations = {
            f"{row['method']}:{row['state']}": int(row["count"]) for row in operation_rows
        }
        heads = {
            str(row["aggregate_key"]): str(row["revision_digest"])
            for row in connection.execute(
                "select aggregate_key, revision_digest from working_heads where project_id=?",
                (project_id,),
            )
        }
        source_count = int(
            connection.execute(
                "select count(*) from artifacts where project_id=?", (project_id,)
            ).fetchone()[0]
        )
        memory_projects = [
            str(row[0])
            for row in connection.execute(
                "select distinct project_id from memory_records where project_id=?",
                (project_id,),
            )
        ]
        receipt_payloads = [
            cast(dict[str, Any], json.loads(str(row[0])))
            for row in connection.execute(
                "select payload_json from receipts where project_id=? order by created_at",
                (project_id,),
            )
        ]
        closure = current_content(connection, project_id, heads, "CLOSURE:")
        export = current_content(connection, project_id, heads, "EXPORT:")
        hypothesis_revisions = int(
            connection.execute(
                "select count(*) from semantic_revisions where project_id=? "
                "and entity_type='HYPOTHESIS'",
                (project_id,),
            ).fetchone()[0]
        )
    checks = {
        "four_sources_connected": source_count == 4,
        "two_oauth_cycles_succeeded": operations.get("thread/input:SUCCEEDED", 0) == 2,
        "outcome_recorded": any(key.startswith("OUTCOME:") for key in heads),
        "hypothesis_history_and_restore": hypothesis_revisions >= 3,
        "closure_closed": closure.get("status") == "CLOSED",
        "export_local_sealed": export.get("release_state") == "LOCAL_SEALED",
        "memory_project_isolated": memory_projects == [] or memory_projects == [project_id],
        "no_failed_operations": not any(key.endswith(":FAILED") for key in operations),
        "closure_receipt_present": any(
            receipt.get("receipt_type") == "CLOSURE" for receipt in receipt_payloads
        ),
        "export_receipt_present": any(
            receipt.get("receipt_type") == "EXPORT" for receipt in receipt_payloads
        ),
        "no_semantic_truth_receipt": all(
            receipt.get("semantic_truth_certified") is False for receipt in receipt_payloads
        ),
    }
    payload = {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "project_id": project_id,
        "checks": checks,
        "source_count": source_count,
        "hypothesis_revision_count": hypothesis_revisions,
        "operation_summary": operations,
        "receipt_count": len(receipt_payloads),
        "external_field_results": "NOT_RUN",
    }
    arguments.json_out.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    arguments.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# THOTH Live General-Project E2E",
        "",
        f"Verdict: **{payload['verdict']}**",
        "",
        f"Project: `{project_id}`",
        "",
        f"Sources: {source_count}; hypothesis revisions: {hypothesis_revisions}; "
        f"receipts: {len(receipt_payloads)}",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {'PASS' if passed else 'FAIL'} — `{name}`" for name, passed in checks.items())
    lines.extend(
        [
            "",
            "External researcher responses, field usefulness, WTP and deployment "
            "accreditation remain `NOT_RUN`.",
        ]
    )
    arguments.markdown_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": payload["verdict"], "project_id": project_id}))
    if payload["verdict"] != "PASS":
        raise SystemExit(1)


def current_content(
    connection: sqlite3.Connection,
    project_id: str,
    heads: dict[str, str],
    prefix: str,
) -> dict[str, Any]:
    digest = next((value for key, value in heads.items() if key.startswith(prefix)), None)
    if digest is None:
        return {}
    row = connection.execute(
        "select s.content_json from semantic_revisions r join entity_snapshots s "
        "on s.snapshot_id=r.snapshot_id where r.project_id=? and r.revision_digest=?",
        (project_id, digest),
    ).fetchone()
    return {} if row is None else cast(dict[str, Any], json.loads(str(row[0])))


if __name__ == "__main__":
    main()
