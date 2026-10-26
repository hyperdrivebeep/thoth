"""Normal source/Thread entry consumes new observations; no real provider is used."""

from pathlib import Path

from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.apps.runtime import create_runtime
from thoth.domain.enums import ModelRole


async def test_new_lineage_observation_coverage_and_cycle_consumers(tmp_path: Path) -> None:
    model = ControlledResearchModel(one=True, discover=True)
    runtime = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "p",
                        "name": "Context contract",
                        "cutoff_at": "2026-10-26T00:00:00Z",
                    },
                )
            )
        )
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "measurements.md").write_text(
            "# Measurements\n\nRelease beta\n\n```text\n"
            "latency count=100 value=1.557 seconds\n```\n",
            encoding="utf-8",
        )
        inputs = {
            "project_id": "p",
            "relative_path": "measurements.md",
            "media_type": "text/markdown",
            "authority": "INFORMAL",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
        }
        original = value(
            await runtime.bus.dispatch(request("project/source/connect", "source", inputs))
        )
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "ask",
                    {
                        "project_id": "p",
                        "problem": "Explain latency and propose possible checks.",
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
                        "thread_id": admitted["thread_id"],
                        "expected_result_contract": "v5",
                        "view": "FULL",
                    },
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED", state.get("operation_error")
        roles = {r.role for r in model.calls}
        assert {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.HYPOTHESIS_GENERATOR,
            ModelRole.HYPOTHESIS_REVIEWER,
            ModelRole.ACTION_PLANNER,
        } <= roles
        for call in model.calls:
            coverage = call.context_pack.research_context["retrieval_coverage"]
            assert isinstance(coverage, dict)
            assert coverage["input_span_refs"] == [s.span_id for s in call.context_pack.evidence]
            assert coverage["catalog_count"] == 5 and coverage["eligible_count"] == 5
            assert coverage["role"] == call.role.value
            assert "Release beta" in {s.exact_text for s in call.context_pack.evidence}
            assert call.context_pack.research_context["context_projection_version"] == "2.0.0"
            assert call.context_pack.research_context["context_bundle"]
        assert state["current_result"]["source_context_version"] == "2.2.0"
        assert state["current_result"]["result"]["retrieval_coverage"]["input_span_count"] == 5
        assert (
            state["current_result"]["result"]["retrieval_coverage"]["role"]
            == ModelRole.ACTION_PLANNER.value
        )
        digest = state["current_result"]["source_context_digest"]
        # Reconnecting creates a new lineage. The old record does not gain a rewritten basis.
        second = value(
            await runtime.bus.dispatch(request("project/source/connect", "reparse", inputs))
        )
        assert second["source_version_id"] != original["source_version_id"]
        readback = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "again",
                    {
                        "project_id": "p",
                        "thread_id": admitted["thread_id"],
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert readback["current_result"]["source_context_digest"] == digest
    finally:
        runtime.close()
