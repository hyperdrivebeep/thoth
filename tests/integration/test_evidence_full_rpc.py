from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_evidence_source_link_conflict_packet_and_audit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text("# Plan\n\nTarget latency is below 1 ms.\n", encoding="utf-8")
    (inbox / "report.md").write_text("# Report\n\nObserved latency is 2 ms.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    project_id = "project:evidence-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "ev-project",
                    {
                        "project_id": project_id,
                        "name": "Evidence full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        connected_plan = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "ev-connect-plan",
                    {
                        "project_id": project_id,
                        "relative_path": "plan.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        connected_report = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "ev-connect-report",
                    {
                        "project_id": project_id,
                        "relative_path": "report.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        plan_artifact = cast(dict[str, JsonValue], connected_plan["artifact"])
        report_artifact = cast(dict[str, JsonValue], connected_report["artifact"])
        plan_source = cast(dict[str, JsonValue], connected_plan["source"])

        source_add = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/add",
                    "ev-source-add",
                    {
                        "project_id": project_id,
                        "artifact_id": plan_artifact["artifact_id"],
                        "rights": "PROJECT_INTERNAL",
                    },
                )
            )
        )
        assert (
            cast(dict[str, JsonValue], source_add["source"])["source_id"]
            == plan_source["source_id"]
        )

        metadata = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/metadata/correct",
                    "ev-source-correct",
                    {
                        "project_id": project_id,
                        "source_id": plan_source["source_id"],
                        "rights": "REUSE_ALLOWED",
                        "official_copy_basis": "Project owner verified official copy",
                    },
                )
            )
        )
        corrected_source = cast(dict[str, JsonValue], metadata["source"])
        assert corrected_source["supersedes_source_id"] == plan_source["source_id"]

        refreshed = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/refresh",
                    "ev-source-refresh",
                    {
                        "project_id": project_id,
                        "source_id": corrected_source["source_id"],
                        "artifact_id": report_artifact["artifact_id"],
                        "connector_ref": "project-source",
                        "version": "v2",
                        "rights": "PROJECT_INTERNAL",
                    },
                )
            )
        )
        assert (
            cast(dict[str, JsonValue], refreshed["source"])["supersedes_source_id"]
            == corrected_source["source_id"]
        )

        evidence_list = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/list",
                    "ev-list",
                    {"project_id": project_id},
                )
            )
        )
        spans = cast(list[dict[str, JsonValue]], evidence_list["spans"])
        plan_span = next(span for span in spans if "Target latency" in str(span["exact_text"]))
        report_span = next(span for span in spans if "Observed latency" in str(span["exact_text"]))

        proposed = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/link/propose",
                    "ev-link-propose",
                    {
                        "project_id": project_id,
                        "target_type": "DECISION_OBJECT",
                        "target_id": "object:latency",
                        "relation": "SUPPORTS",
                        "span_ids": [plan_span["span_id"]],
                        "observed_statement": "The official target is below 1 ms.",
                        "conditions": {"configuration": "baseline"},
                        "independence_group": "plan-lineage",
                    },
                )
            )
        )
        support = cast(dict[str, JsonValue], proposed["evidence"])
        support_id = str(support["evidence_id"])
        revalidated = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/revalidate",
                    "ev-revalidate",
                    {"project_id": project_id, "evidence_id": support_id},
                )
            )
        )
        validated = cast(dict[str, JsonValue], revalidated["evidence"])
        assert validated["support_status"] == "SUPPORTED"
        validated_id = str(validated["evidence_id"])

        contradicted = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/link/propose",
                    "ev-link-contradict",
                    {
                        "project_id": project_id,
                        "target_type": "DECISION_OBJECT",
                        "target_id": "object:latency",
                        "relation": "CONTRADICTS",
                        "span_ids": [report_span["span_id"]],
                        "observed_statement": "The observed value is 2 ms.",
                        "conditions": {"configuration": "current"},
                        "independence_group": "report-lineage",
                    },
                )
            )
        )
        counter = cast(dict[str, JsonValue], contradicted["evidence"])
        counter_id = str(counter["evidence_id"])
        corrected_link = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/link/correct",
                    "ev-link-correct",
                    {
                        "project_id": project_id,
                        "evidence_id": counter_id,
                        "target_type": "DECISION_OBJECT",
                        "target_id": "object:latency",
                        "relation": "QUALIFIES",
                        "span_ids": [report_span["span_id"]],
                        "observed_statement": (
                            "The observed value is 2 ms under current configuration."
                        ),
                        "conditions": {"configuration": "current"},
                        "independence_group": "report-lineage",
                    },
                )
            )
        )
        corrected = cast(dict[str, JsonValue], corrected_link["evidence"])
        assert corrected["supersedes_evidence_id"] == counter_id

        challenged = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/challenge",
                    "ev-challenge",
                    {
                        "project_id": project_id,
                        "evidence_ids": [validated_id, corrected["evidence_id"]],
                        "field": "latency",
                        "reason": "target and result differ under distinct conditions",
                    },
                )
            )
        )
        conflict = cast(dict[str, JsonValue], challenged["conflict"])
        conflict_id = str(conflict["conflict_id"])
        conflicts = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/conflict/list",
                    "ev-conflict-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], conflicts["conflicts"])) == 1
        conflict_read = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/conflict/read",
                    "ev-conflict-read",
                    {"project_id": project_id, "conflict_id": conflict_id},
                )
            )
        )
        assert isinstance(conflict_read["conflict"], dict)

        packet = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/packet/read",
                    "ev-packet",
                    {"project_id": project_id, "target_id": "object:latency"},
                )
            )
        )
        packet_value = cast(dict[str, JsonValue], packet["packet"])
        assert len(cast(list[object], packet_value["support"])) >= 1
        assert len(cast(list[object], packet_value["conflicts"])) == 1

        correction = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/span/correct",
                    "ev-span-correct",
                    {
                        "project_id": project_id,
                        "span_id": report_span["span_id"],
                        "corrected_text": "Observed latency is 2.0 ms.",
                        "reason": "normalize decimal representation without rewriting source",
                    },
                )
            )
        )
        assert isinstance(correction["correction"], dict)
        span_read = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/read",
                    "ev-span-read",
                    {"project_id": project_id, "span_id": report_span["span_id"]},
                )
            )
        )
        assert len(cast(list[object], span_read["corrections"])) == 1
        link_read = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/read",
                    "ev-link-read",
                    {"project_id": project_id, "evidence_id": validated_id},
                )
            )
        )
        assert isinstance(link_read["evidence"], dict)
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/audit/read",
                    "ev-audit",
                    {"project_id": project_id, "offset": 0, "limit": 100},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 8
    finally:
        runtime.close()
