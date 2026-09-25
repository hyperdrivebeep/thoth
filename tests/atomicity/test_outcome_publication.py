from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, event
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.domain.canonical import head_set_digest


@pytest.mark.parametrize(
    "method,table",
    [
        ("outcome/series/create", "outcome_series"),
        ("outcome/observation/link", "outcome_series"),
        ("outcome/assess", "outcome_assessments"),
        ("outcome/reassess", "outcome_assessments"),
        ("outcome/attribution/assess", "outcome_attributions"),
        ("outcome/changeSet/propose", "outcome_change_sets"),
        ("outcome/followup/generate", "outcome_audit"),
        ("outcome/impact/propose", "outcome_impacts"),
    ],
)
async def test_independent_outcome_writers_rollback_and_reopen_without_execution_requirement(
    tmp_path: Path, method: str, table: str
) -> None:
    needs_assessment = method in {
        "outcome/reassess",
        "outcome/attribution/assess",
        "outcome/changeSet/propose",
        "outcome/followup/generate",
    }
    runtime, ctx = await prepare_outcome(
        tmp_path, create_series=method != "outcome/series/create", assess=needs_assessment
    )
    project, spans = ctx["project"], ctx["spans"]
    fields: dict[str, object] = {}
    reads: list[str] = []
    if method == "outcome/series/create":
        fields = ctx["series_input"]
    elif not needs_assessment:
        series = ctx["series"]
        reads = [f"revision:{series['revision_digest']}"]
        fields = {
            "outcome_series_id": series["outcome_series_id"],
            "expected_series_revision": series["revision"],
            "assessment_phase": "INTERIM",
            "observation_refs": spans,
        }
        if method == "outcome/observation/link":
            fields.update(completeness="PARTIAL", evidence_refs=spans)
        elif method == "outcome/assess":
            fields.update(profile_version=1, comparator_refs=spans, assumptions=[])
        else:
            fields = {
                "outcome_series_id": series["outcome_series_id"],
                "broader_window": "later follow-up",
                "impact_profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                "evidence_refs": spans,
                "attribution_design_ref": "TEMPORAL_ONLY",
            }
    else:
        assessment = ctx["assessment"]
        reads = list(
            dict.fromkeys(
                [
                    f"revision:{assessment['revision_digest']}",
                    *assessment["actual_observation_refs"],
                    *assessment["comparator_refs"],
                ]
            )
        )
        fields = {"outcome_assessment_id": assessment["outcome_assessment_id"]}
        if method == "outcome/reassess":
            fields.update(
                expected_revision_digest=assessment["revision_digest"],
                new_evidence_refs=spans,
                reason="Check again",
            )
        elif method == "outcome/attribution/assess":
            fields.update(
                attribution_method="CONTRIBUTION_ANALYSIS",
                contextual_factor_refs=[],
                evidence_refs=spans,
            )
        elif method == "outcome/changeSet/propose":
            fields.update(
                proposed_entity_changes={},
                impact_policy_ref="policy:candidate-only",
                expected_project_head_set=head_set_digest(runtime.ledger.read_heads(project)),
            )
        else:
            fields.update(follow_up_scope="Resolve current limitations")
    command = request(method, "fault", {"project_id": project, **fields})
    before = snapshot(runtime.ledger.engine)
    reached = False

    def after_sql(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        nonlocal reached
        if statement.lstrip().upper().startswith("INSERT INTO " + table.upper() + " "):
            reached = True
            assert connection.in_transaction()
            raise RuntimeError("independent Outcome SQL fault")

    try:
        event.listen(runtime.ledger.engine, "after_cursor_execute", after_sql)
        try:
            failed = await runtime.bus.dispatch(command)
        finally:
            event.remove(runtime.ledger.engine, "after_cursor_execute", after_sql)
        assert reached and failed.error is not None
        allowed = failed_command_allowances(
            runtime.ledger.engine, command, expected_reads=tuple(reads)
        )
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        result = value(
            await reopened.bus.dispatch(request(method, "retry", dict(command.params.input)))
        )
        assert result
        after = snapshot(reopened.ledger.engine)
        assert after["plan_executions"] == before["plan_executions"]
        assert after["step_execution_attempts"] == before["step_execution_attempts"]
    finally:
        reopened.close()
