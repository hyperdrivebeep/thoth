"""Actual mock model inputs and stored reads consume the same source arithmetic."""

from pathlib import Path
from typing import cast

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.unit.test_numeric_series import numeric_text

from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload
from thoth.domain.derived_calculation import SOURCE_ARITHMETIC_CONTRACT, CheckedObservations
from thoth.domain.enums import ModelRole
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import ResearchAttempt, RevisionRef, ThreadRequestRevision


async def test_reviewer_adjudicator_generator_receive_calculations_and_result_read_keeps_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ControlledResearchModel(one=True)
    runtime = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    host = research_host(runtime)
    publish = host.publish
    checked_rejection = False

    def guarded_publish(
        attempt: ResearchAttempt,
        accepted: ThreadRequestRevision,
        work: ResearchWork,
        phase: str,
        result: dict[str, object],
        gaps: tuple[str, ...],
        terminal: str | None = None,
        check: bool = True,
    ) -> RevisionRef:
        nonlocal checked_rejection
        if not checked_rejection and "checked_observations" in result:
            bad = CheckedObservations.model_validate(result["checked_observations"]).model_dump(
                mode="json"
            )
            bad["calculations"][0]["value"] = "99"
            before = snapshot(runtime.ledger.engine)
            pending = tuple(work.pending_revisions)
            with pytest.raises(ValueError, match="NUMERIC_OUTPUT_DIGEST_MISMATCH"):
                publish(
                    attempt,
                    accepted,
                    work,
                    phase,
                    {**result, "checked_observations": bad},
                    gaps,
                    terminal,
                    check,
                )
            assert_phase_delta(before, snapshot(runtime.ledger.engine))
            assert tuple(work.pending_revisions) == pending
            checked_rejection = True
        return publish(attempt, accepted, work, phase, result, gaps, terminal, check)

    monkeypatch.setattr(host, "publish", guarded_publish)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {"project_id": "p", "name": "Arithmetic", "cutoff_at": "2026-10-27T00:00:00Z"},
                )
            )
        )
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "measurements.md").write_text(numeric_text("Unrelated engine"), encoding="utf-8")
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": "p",
                        "relative_path": "measurements.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Compare the printed measurements.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "read",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "view": "FULL",
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED", state.get("operation_error")
        assert checked_rejection
        wanted = {
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.HYPOTHESIS_GENERATOR,
        }
        seen: set[ModelRole] = set()
        assert (
            len(model.calls) == 7
        )  # Existing planner/ranker/review/adjudication/generator/review/action.
        for call in model.calls:
            view = call.context_pack.research_context.get("checked_observations")
            if call.role in wanted:
                assert isinstance(view, dict)
                numeric = cast(dict[str, object], view)
                # Model projection groups three operations per interval; stored records stay full.
                rows = cast(list[dict[str, object]], numeric["intervals"])
                assert numeric["contract"] == SOURCE_ARITHMETIC_CONTRACT
                assert any(row["ratio"] == "62.280000" for row in rows)
                assert any(row["log_log_slope"] == "1.794349" for row in rows)
                assert len(rows) == 6 and all(
                    {"ratio", "difference", "log_log_slope"} <= row.keys() for row in rows
                )
                ids = {span.span_id for span in call.context_pack.evidence}
                assert all(
                    set(cast(list[str] | tuple[str, ...], row["operand_refs"])) <= ids
                    for row in rows
                )
                assert numeric["not_delivered"] == 0
                seen.add(call.role)
            else:
                assert view is None
        assert seen == wanted
        manifest = state["current_result"]
        observed = CheckedObservations.model_validate(manifest["result"]["checked_observations"])
        assert len(observed.series) == 3 and len(observed.calculations) == 18
        assert {d.role for d in observed.deliveries} == {r.value for r in wanted}
        before = canonical_payload(observed)
        summary = value(
            await runtime.bus.query(
                request(
                    "thread/read", "summary", {"project_id": "p", "thread_id": started["thread_id"]}
                )
            )
        )
        compact = summary["current_result"]["result"]["checked_observations"]
        assert "series" not in compact and len(compact["display_rows"]) == 18
        again = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "again",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "view": "FULL",
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert (
            canonical_payload(
                CheckedObservations.model_validate(
                    again["current_result"]["result"]["checked_observations"]
                )
            )
            == before
        )
    finally:
        runtime.close()
