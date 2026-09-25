from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, value

from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.protocol.jsonrpc import JsonRpcRequest, RpcErrorCode


def request(
    method: str,
    key: str,
    input_value: dict[str, object],
    *,
    field_session_id: str | None = None,
) -> JsonRpcRequest:
    meta: dict[str, object] = {"idempotencyKey": key}
    if field_session_id is not None:
        meta["fieldSessionId"] = field_session_id
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": meta, "input": input_value},
        }
    )


@pytest.mark.asyncio
async def test_sealed_abc_session_instruments_normal_thread_and_exports_not_run_bundle(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        authenticated = AuthenticatedActorContext(
            actor_id="human:field-scorer",
            session_id="auth-session:a12",
            project_id=project_id,
            role_assignment_id="role:a12:scorer",
            role="field-scorer",
            capabilities=("READ", "WRITE"),
            data_scopes=("PROJECT",),
        )
        seal = value(
            await runtime.bus.dispatch(
                request(
                    "field/protocol/seal",
                    "a12-seal",
                    {
                        "project_id": project_id,
                        "protocol_version": "abc:1.0.0",
                        "case_digests": {"case:dataset-audit": "a" * 64},
                        "arms": ["A", "B", "C"],
                        "sequence_matrix": ["ABC", "BCA", "CAB", "ACB", "CBA", "BAC"],
                        "baseline_toolchain": ["viewer", "spreadsheet", "markdown", "git"],
                        "thresholds": {
                            "quality_noninferiority_bps": 0,
                            "active_time_reduction_bps": 2_000,
                            "manual_burden_reduction_bps": 3_000,
                        },
                        "hard_zero_metrics": [
                            "critical_false_acceptance",
                            "future_oracle_leakage",
                            "cross_project_leakage",
                            "unauthorized_r3",
                        ],
                    },
                )
            )
        )
        protocol = cast(dict[str, JsonValue], seal["protocol"])
        assert protocol["state"] == "SEALED_BEFORE_RESULTS"
        assert protocol["external_results"] == "NOT_RUN"

        started = value(
            await runtime.bus.dispatch(
                request(
                    "field/session/start",
                    "a12-session-start",
                    {
                        "project_id": project_id,
                        "protocol_digest": protocol["protocol_digest"],
                        "reviewer_external_ref": "researcher@example.com",
                        "case_id": "case:dataset-audit",
                        "arm": "C",
                        "sequence_position": 1,
                    },
                )
            )
        )
        session = cast(dict[str, JsonValue], started["session"])
        session_id = str(session["session_id"])
        assert session["reviewer_pseudonym"]
        assert "researcher@example.com" not in str(session)

        first_read = await runtime.bus.dispatch(
            request(
                "project/read",
                "a12-session-scope-replay",
                {"project_id": project_id},
            )
        )
        assert first_read.error is None
        scoped_replay = await runtime.bus.dispatch(
            request(
                "project/read",
                "a12-session-scope-replay",
                {"project_id": project_id},
                field_session_id=session_id,
            )
        )
        assert scoped_replay.error is not None
        assert scoped_replay.error.code == RpcErrorCode.IDEMPOTENCY_CONFLICT

        reseal = await runtime.bus.dispatch(
            request(
                "field/protocol/seal",
                "a12-reseal",
                {
                    "project_id": project_id,
                    "protocol_version": "abc:2.0.0",
                    "case_digests": {"case:dataset-audit": "b" * 64},
                    "arms": ["A", "B", "C"],
                    "sequence_matrix": ["ABC"],
                    "baseline_toolchain": ["changed-after-results"],
                    "thresholds": {"active_time_reduction_bps": 9_999},
                    "hard_zero_metrics": ["critical_false_acceptance"],
                },
            )
        )
        assert reseal.error is not None
        assert "already sealed" in reseal.error.message

        unsafe_event = await runtime.bus.dispatch(
            request(
                "field/event/record",
                "a12-unsafe-event",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "event_type": "SOURCE_OPEN",
                    "metadata": {"raw_text": "private participant content"},
                },
            )
        )
        assert unsafe_event.error is not None
        assert "privacy-sensitive" in unsafe_event.error.message

        early_score = await runtime.bus.dispatch(
            request(
                "field/score/record",
                "a12-early-score",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "scorer_external_ref": "expert@example.com",
                    "gold_issue_total": 5,
                    "critical_issue_detected": 4,
                    "decision_completeness_bps": 8_000,
                    "source_span_valid_count": 4,
                    "source_span_invalid_count": 0,
                    "hard_zero_values": {
                        "critical_false_acceptance": 0,
                        "future_oracle_leakage": 0,
                        "cross_project_leakage": 0,
                        "unauthorized_r3": 0,
                    },
                },
            )
        )
        assert early_score.error is not None
        assert "ended session" in early_score.error.message

        heads_before_invalid_session = dict(runtime.ledger.read_heads(project_id))
        invalid_instrumentation = await runtime.bus.dispatch(
            request(
                "thread/input",
                "a12-invalid-session",
                {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                field_session_id="field-session:missing",
            )
        )
        assert invalid_instrumentation.error is not None
        assert "field session not found" in invalid_instrumentation.error.message
        assert dict(runtime.ledger.read_heads(project_id)) == heads_before_invalid_session

        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a12-instrumented-thread",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                    field_session_id=session_id,
                )
            )
        )
        assert analyzed["commit"]
        value(
            await runtime.bus.dispatch(
                request(
                    "field/event/record",
                    "a12-app-switch",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "event_type": "APP_SWITCH",
                        "metric_delta": 1,
                        "metadata": {"from_tool_code": "viewer", "to_tool_code": "thoth"},
                    },
                )
            )
        )
        ended = value(
            await runtime.bus.dispatch(
                request(
                    "field/session/end",
                    "a12-session-end",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "timeout": False,
                    },
                )
            )
        )
        metrics = cast(dict[str, JsonValue], ended["metrics"])
        assert int(str(metrics["rpc_operation_count"])) >= 1
        assert metrics["app_switch_count"] == 1

        with authenticated_actor_scope(authenticated):
            impersonated_score = await runtime.bus.dispatch(
                request(
                    "field/score/record",
                    "a12-impersonated-score",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "scorer_external_ref": "human:different-scorer",
                        "gold_issue_total": 5,
                        "critical_issue_detected": 4,
                        "decision_completeness_bps": 8_000,
                        "source_span_valid_count": 4,
                        "source_span_invalid_count": 0,
                        "hard_zero_values": {
                            "critical_false_acceptance": 0,
                            "future_oracle_leakage": 0,
                            "cross_project_leakage": 0,
                            "unauthorized_r3": 0,
                        },
                    },
                )
            )
        assert impersonated_score.error is not None
        assert impersonated_score.error.code == RpcErrorCode.AUTHORIZATION_DENIED

        reviewer_context = authenticated.model_copy(
            update={
                "actor_id": "researcher@example.com",
                "session_id": "auth-session:a12:reviewer",
                "role_assignment_id": "role:a12:reviewer",
                "role": "field-reviewer",
            }
        )
        with authenticated_actor_scope(reviewer_context):
            reviewer_score = await runtime.bus.dispatch(
                request(
                    "field/score/record",
                    "a12-reviewer-score",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "scorer_external_ref": "researcher@example.com",
                        "gold_issue_total": 5,
                        "critical_issue_detected": 4,
                        "decision_completeness_bps": 8_000,
                        "source_span_valid_count": 4,
                        "source_span_invalid_count": 0,
                        "hard_zero_values": {
                            "critical_false_acceptance": 0,
                            "future_oracle_leakage": 0,
                            "cross_project_leakage": 0,
                            "unauthorized_r3": 0,
                        },
                    },
                )
            )
        assert reviewer_score.error is not None
        assert "independent blind score" in reviewer_score.error.message

        value(
            await runtime.bus.dispatch(
                request(
                    "field/score/record",
                    "a12-score",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "scorer_external_ref": "expert@example.com",
                        "gold_issue_total": 5,
                        "critical_issue_detected": 4,
                        "decision_completeness_bps": 8_000,
                        "source_span_valid_count": 4,
                        "source_span_invalid_count": 0,
                        "hard_zero_values": {
                            "critical_false_acceptance": 0,
                            "future_oracle_leakage": 0,
                            "cross_project_leakage": 0,
                            "unauthorized_r3": 0,
                        },
                    },
                )
            )
        )
        exported = value(
            await runtime.bus.dispatch(
                request(
                    "field/export/build",
                    "a12-export",
                    {
                        "project_id": project_id,
                        "protocol_digest": protocol["protocol_digest"],
                        "purpose": "independent blinded analysis",
                    },
                )
            )
        )
        bundle = cast(dict[str, JsonValue], exported["bundle"])
        assert bundle["external_results"] == "NOT_RUN"
        assert bundle["field_validated"] is False
        assert bundle["d6_claimed"] is False
        assert bundle["privacy_safe"] is True
        assert "researcher@example.com" not in str(bundle)
        assert "expert@example.com" not in str(bundle)
        assert cast(list[object], bundle["events"])
        assert cast(list[object], bundle["scores"])
    finally:
        runtime.close()
