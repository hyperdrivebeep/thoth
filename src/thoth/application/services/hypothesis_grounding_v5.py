"""Opt-in v5 acceptance; default generation changes only when storage/read consumers are ready."""

from dataclasses import replace
from typing import cast

from pydantic import ValidationError

from thoth.application.reducers import validate_hypothesis_portfolio
from thoth.application.services.english_prose import record_english_check
from thoth.application.services.hypothesis_contract_v3 import GeneratorContract
from thoth.application.services.hypothesis_grounding_fill import (
    grounding_fill_context,
    merge_grounded_fill,
)
from thoth.application.services.hypothesis_grounding_hold import GroundingPublicationHold
from thoth.application.services.hypothesis_grounding_settle import settle_grounded_portfolio
from thoth.application.services.hypothesis_table_filler import (
    BUDGET_HOLDS,
    TABLE_FILLER_MAX_OUTPUT_TOKENS,
    UNUSABLE_ANSWER,
    CycleInput,
    count_cells,
)
from thoth.application.services.hypothesis_table_filler import (
    noted as _noted,
)
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis_grounding_result import GroundedPortfolioResult
from thoth.domain.hypothesis_v5 import (
    CONTRACT_V5,
    FILLER_V6,
    HypothesisPortfolioV5,
    TableFillRecordV5,
)
from thoth.domain.hypothesis_v5_wire import (
    ReceivedHypothesisPortfolioV5,
    ReceivedHypothesisTableFillV5,
)
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.prediction_grounding import GroundingStage
from thoth.domain.research_execution import research_work
from thoth.ports.model import ModelExecutionHold, ModelPort


def grounding_generator_contract() -> GeneratorContract:
    return {
        "output_model": ReceivedHypothesisPortfolioV5,
        "prompt_version": CONTRACT_V5,
        "max_output_tokens": 8_000,
    }


def _supplied_context(context: ContextPack, stage: GroundingStage) -> ContextPack:
    work = research_work.get()
    if work is None:
        return context
    capture = work.grounding_stages.get(stage)
    if capture is None:
        raise GroundingPublicationHold("GROUNDING_SOURCE_STAGE_MISSING")
    return context.model_copy(update={"evidence": capture.evidence})


def _with_record(
    result: GroundedPortfolioResult, record: TableFillRecordV5
) -> GroundedPortfolioResult:
    return replace(result, portfolio=cast(HypothesisPortfolioV5, _noted(result.portfolio, record)))


async def fill_grounded_tables(
    model: ModelPort, result: GroundedPortfolioResult, context: ContextPack, command: CycleInput
) -> GroundedPortfolioResult:
    if len(result.portfolio.hypotheses) < 2:
        return result
    cells, opened = count_cells(result.portfolio)
    if not cells:
        return result
    base = TableFillRecordV5(
        state="SKIPPED_FULL", cells=cells, open_before=opened, open_after=opened
    )
    request_context = grounding_fill_context(context, result)
    payload = cast(dict[str, object], request_context.research_context["table_filler"])
    if not payload["tests"]:
        return _with_record(result, base)
    try:
        reply = await model.structured(
            ModelRequest(
                role=ModelRole.HYPOTHESIS_TABLE_FILLER,
                project_id=context.project_id,
                cutoff_at=command.cutoff_at,
                context_pack=request_context,
                output_model=ReceivedHypothesisTableFillV5,
                prompt_version=FILLER_V6,
                model_policy_ref=command.model_policy_ref,
                max_output_tokens=TABLE_FILLER_MAX_OUTPUT_TOKENS,
            )
        )
    except GroundingPublicationHold:
        raise
    except (ModelExecutionHold, ValidationError) as error:
        reason = UNUSABLE_ANSWER if isinstance(error, ValidationError) else str(error)
        return _with_record(
            result,
            base.model_copy(
                update={
                    "state": "SKIPPED_BUDGET" if reason in BUDGET_HOLDS else "FAILED",
                    "reason_code": reason[:200],
                }
            ),
        )
    work = research_work.get()
    merged, changed, answers = merge_grounded_fill(
        result,
        reply.output,
        _supplied_context(request_context, "FILLER"),
        None if work is None else work.response_language,
    )
    _, open_after = count_cells(merged.portfolio)
    return _with_record(
        merged,
        base.model_copy(
            update={
                "state": "CALLED",
                "changed_cells": changed,
                "open_after": open_after,
                "answers": answers,
            }
        ),
    )


async def accepted_grounded_portfolio(
    model: ModelPort,
    received: ReceivedHypothesisPortfolioV5,
    context: ContextPack,
    command: CycleInput,
    require_unknown: bool,
) -> HypothesisPortfolioV5:
    work = research_work.get()
    supplied = _supplied_context(context, "GENERATOR")
    settled = settle_grounded_portfolio(
        received,
        supplied,
        None if work is None else work.response_language,
    )
    filled = await fill_grounded_tables(model, settled, context, command)
    validate_hypothesis_portfolio(
        filled.portfolio,
        evidence=command.evidence,
        require_unknown_alternative=require_unknown,
    )
    record_english_check(filled.portfolio, context)
    if work is not None:
        work.hypothesis_grounding = filled
    return filled.portfolio
