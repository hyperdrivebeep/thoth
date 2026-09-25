"""All-or-nothing generation of a bounded local Action alternative batch."""

from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.action_service import PURPOSES, ActionService
from thoth.domain.action_full import ActionRecord
from thoth.domain.base import DomainModel
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class GenerateInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    object_id: str = Field(min_length=1, max_length=160)
    hypothesis_refs: tuple[str, ...] = ()
    decision_need: str = Field(min_length=1, max_length=5_000)
    evidence_scope: tuple[str, ...]
    purpose_hints: tuple[str, ...] = ()
    budget_policy_ref: str | None = Field(default=None, max_length=160)


async def generate_actions(
    value: dict[str, JsonValue],
    *,
    service: ActionService,
) -> dict[str, JsonValue]:
    request = GenerateInput.model_validate(value)
    with service.atomic():
        purpose_hints = tuple(item for item in request.purpose_hints if item in PURPOSES)
        purposes = tuple(
            dict.fromkeys(
                (
                    *purpose_hints,
                    "INFORMATION_ACQUISITION",
                    "SIMULATION",
                    "ANALYSIS_COMPUTATION",
                )
            )
        )[:3]
        portfolio_id = f"action-portfolio:{request.object_id}"
        records: list[ActionRecord] = []
        commits: list[JsonValue] = []
        for purpose in purposes:
            specification: dict[str, object] = {
                "description": f"Bounded {purpose.lower()} candidate for {request.decision_need}",
                "expected_observation_or_change": {
                    "description": "decision-relevant observation candidate",
                    "status": "CANDIDATE",
                },
                "effect_completeness_confirmed": True,
                "stop_conditions": ("budget exhausted", "sufficient information acquired"),
                "observability": "operation receipt and result digest",
                "effect_vector": {
                    "effect_completeness_confirmed": True,
                    "runs_untrusted_code": purpose == "SIMULATION",
                    "sandbox_required": purpose == "SIMULATION",
                    "external_write": False,
                    "physical_action": False,
                    "changes_official_baseline": False,
                },
            }
            try:
                record, _duplicates, commit = service.create(
                    project_id=request.project_id,
                    object_id=request.object_id,
                    portfolio_id=portfolio_id,
                    hypothesis_refs=request.hypothesis_refs,
                    primary_purpose=purpose,
                    secondary_purposes=(),
                    specification=specification,
                    evidence_refs=request.evidence_scope,
                )
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            records.append(record)
            commits.append(cast(JsonValue, commit.model_dump(mode="json")))
        return cast(
            dict[str, JsonValue],
            {
                "actions": [item.model_dump(mode="json") for item in records],
                "alternative_gaps": [],
                "expected_observations": [item.expected_observation_or_change for item in records],
                "impact_candidates": [item.impact_set for item in records],
                "budget_policy_ref": request.budget_policy_ref,
                "commits": commits,
            },
        )
