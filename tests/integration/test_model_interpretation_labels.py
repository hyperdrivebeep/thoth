"""Mock normal-RPC runs: what each later role actually receives for earlier-role statements."""

from dataclasses import replace
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.unit.test_numeric_series import numeric_text

from thoth.adapters.models.codex_oauth import prompt_envelope
from thoth.apps.runtime import create_runtime
from thoth.domain.absence_claim import ReviewProposalE2
from thoth.domain.derived_calculation import NUMERIC_CITATION_INSTRUCTION
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import ConfirmedFinding
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_interpretation import MODEL_INTERPRETATION_CONTRACT

OPEN_ITEM = "측정 조건이 같은지는 자료에서 확인되지 않았다."
FINDING_TEXT = "보고된 시간은 버전마다 다르다."
PLAIN_TEXT = "# Notes\n\nThe service restarted twice during the test window.\n"


class StatementsModel(ControlledResearchModel):
    """The controlled model, plus a planner open item and a reviewer finding citing its input."""

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        output = cast(BaseModel, result.output)
        if request.role == ModelRole.RESEARCH_PLANNER:
            output = output.model_copy(update={"unresolved_interpretations": (OPEN_ITEM,)})
        elif request.role == ModelRole.SEMANTIC_REVIEWER:
            proposal = ReviewProposalE2.model_validate(output.model_dump())
            output = proposal.model_copy(
                update={
                    "confirmed_findings": (
                        ConfirmedFinding(
                            statement=FINDING_TEXT,
                            evidence_refs=(request.context_pack.evidence[0].span_id,),
                            kind="DOCUMENT_FACT",
                        ),
                    )
                }
            )
        return replace(result, output=cast(T, output))


async def run(tmp_path: Path, text: str) -> tuple[StatementsModel, dict[str, object]]:
    model = StatementsModel(one=True)
    runtime = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {"project_id": "p", "name": "Labels", "cutoff_at": "2026-10-27T00:00:00Z"},
                )
            )
        )
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "measurements.md").write_text(text, encoding="utf-8")
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
        return model, cast(dict[str, object], state["current_result"]["result"])
    finally:
        runtime.close()


def received(model: StatementsModel, role: ModelRole) -> dict[str, object]:
    calls = [c for c in model.calls if c.role == role]
    assert len(calls) == 1, role
    return calls[0].context_pack.research_context


def view_items(research: dict[str, object]) -> list[dict[str, object]]:
    view = cast(dict[str, object], research["model_interpretations"])
    assert view["schema_version"] == "1.0.0" and view["contract"] == MODEL_INTERPRETATION_CONTRACT
    return cast(list[dict[str, object]], view["items"])


async def test_later_roles_receive_labelled_interpretations_and_the_stored_result_is_unchanged(
    tmp_path: Path,
) -> None:
    model, result = await run(tmp_path, PLAIN_TEXT)
    assert len(model.calls) == 7
    planner: dict[str, object] = {
        "label": "MODEL_INTERPRETATION",
        "producer_role": "RESEARCH_PLANNER",
        "item_kind": "PLANNER_OPEN_ITEM",
        "check": "NOT_CHECKED",
        "statement": OPEN_ITEM,
        "evidence_refs": [],
    }
    for role in (
        ModelRole.HYPOTHESIS_GENERATOR,
        ModelRole.HYPOTHESIS_REVIEWER,
        ModelRole.ACTION_PLANNER,
    ):
        research = received(model, role)
        assert "confirmed_findings" not in research
        assert "interpretation_open_items" not in research
        finding, open_item = view_items(research)
        assert finding["producer_role"] == "SEMANTIC_REVIEWER"
        assert finding["check"] == "CITED_SPAN_IDS_ONLY"
        assert finding["label"] == "MODEL_INTERPRETATION"
        assert finding["statement"] == FINDING_TEXT and finding["finding_kind"] == "DOCUMENT_FACT"
        assert open_item == planner
        # The sentence is part of what the provider is actually sent.
        call = next(c for c in model.calls if c.role == role)
        assert MODEL_INTERPRETATION_CONTRACT in prompt_envelope(call)
    # The reranker, reviewer and adjudicator saw only the planner's item; no finding existed yet.
    for role in (
        ModelRole.EVIDENCE_RERANKER,
        ModelRole.SEMANTIC_REVIEWER,
        ModelRole.REVIEW_ADJUDICATOR,
    ):
        research = received(model, role)
        assert "interpretation_open_items" not in research
        assert view_items(research) == [planner]
    # The planner wrote the open item; nothing existed before its call.
    assert "model_interpretations" not in received(model, ModelRole.RESEARCH_PLANNER)
    # The stored result keeps the original fields in their original shape.
    findings = cast(list[dict[str, object]], result["confirmed_findings"])
    assert [f["statement"] for f in findings] == [FINDING_TEXT]
    assert set(findings[0]) == {"statement", "evidence_refs", "requirement_id", "kind"}
    assert result["interpretation_open_items"] == [OPEN_ITEM]
    assert "model_interpretations" not in result


async def test_polars_numbers_get_the_citation_instruction_only_for_reviewer_and_generator(
    tmp_path: Path,
) -> None:
    model, _ = await run(tmp_path, numeric_text("Polars"))
    cited = {ModelRole.SEMANTIC_REVIEWER, ModelRole.HYPOTHESIS_GENERATOR}
    for call in model.calls:
        research = call.context_pack.research_context
        if call.role in cited:
            assert research["numeric_instruction"] == NUMERIC_CITATION_INSTRUCTION
            assert "checked_observations" in research
            assert NUMERIC_CITATION_INSTRUCTION in prompt_envelope(call)
        else:
            assert "numeric_instruction" not in research
            assert NUMERIC_CITATION_INSTRUCTION not in prompt_envelope(call)
    reviewer = next(c for c in model.calls if c.role == ModelRole.SEMANTIC_REVIEWER)
    assert reviewer.prompt_version == "semantic_reviewer.v7"


async def test_a_source_without_printed_series_gets_no_citation_instruction(
    tmp_path: Path,
) -> None:
    model, _ = await run(tmp_path, PLAIN_TEXT)
    for call in model.calls:
        research = call.context_pack.research_context
        assert "numeric_instruction" not in research and "checked_observations" not in research
        assert NUMERIC_CITATION_INSTRUCTION not in prompt_envelope(call)
