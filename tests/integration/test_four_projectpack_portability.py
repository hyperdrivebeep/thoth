from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from thoth.adapters.projectpacks import load_project_pack
from thoth.application.workflows.projectpack_run import ProjectPackRunResult
from thoth.apps.projectpack_execution import run_project_pack
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole, SufficiencyStatus
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL

TModel = TypeVar("TModel", bound=BaseModel)


class GenericProjectPackModel:
    """Domain-neutral QA model: uses only the provided ContextPack, never pack oracle data."""

    control_capability = CONTROLLED_MODEL_CONTROL

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        if request.role in {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.SOURCE_PLANNER,
            ModelRole.HYPOTHESIS_REVIEWER,
        }:
            from tests.integration.test_research_request_v2 import ControlledResearchModel

            return await ControlledResearchModel().structured(request)
        context = request.context_pack
        evidence = context.evidence
        refs = [item.span_id for item in evidence]
        first_ref = refs[0] if refs else None
        second_ref = refs[1] if len(refs) > 1 else first_ref
        if request.role == ModelRole.HYPOTHESIS_GENERATOR:
            payload: dict[str, object] = {
                "portfolio_id": f"portfolio:{context.object_id}",
                "object_id": context.object_id,
                "hypotheses": [
                    self._hypothesis(
                        context.object_id,
                        "hypothesis:measurement-lineage",
                        "A measurement, data, or version-lineage mismatch may explain the gap.",
                        "MEASUREMENT_OBSERVATION",
                        first_ref,
                        "Compare source-bound measurement and version manifests.",
                    ),
                    self._hypothesis(
                        context.object_id,
                        "hypothesis:interface-method",
                        "A method, configuration, or interface mismatch may explain the gap.",
                        "COMPONENT_INTERFACE_SYSTEM",
                        second_ref,
                        (
                            "Compare method and configuration manifests, then replay only "
                            "in isolation."
                        ),
                    ),
                    self._hypothesis(
                        context.object_id,
                        "hypothesis:unknown-reserve",
                        (
                            "An unobserved factor outside the authorized source set may "
                            "explain the gap."
                        ),
                        "OTHER_WITH_DESCRIPTION",
                        None,
                        "Search only authorized project sources for a new causal locus.",
                    ),
                ],
                "status": "TESTABLE",
                "generated_from_head_set": context.input_head_set_digest,
            }
        elif request.role == ModelRole.ACTION_PLANNER:
            payload = {
                "plan_id": f"plan:{context.object_id}",
                "object_id": context.object_id,
                "alternatives": [
                    self._action(
                        context.object_id,
                        "action:evidence-request",
                        ("hypothesis:measurement-lineage",),
                        "EVIDENCE_REQUEST",
                        "Acquire the missing source-bound manifest without changing project state.",
                        first_ref,
                        {},
                        "FULL",
                    ),
                    self._action(
                        context.object_id,
                        "action:sandbox-replay",
                        ("hypothesis:interface-method",),
                        "SANDBOX_REPLAY",
                        (
                            "Replay candidate method or configuration differences in an "
                            "isolated sandbox."
                        ),
                        second_ref,
                        {"runs_untrusted_code": True},
                        "FULL",
                    ),
                    self._action(
                        context.object_id,
                        "action:controlled-rerun",
                        (
                            "hypothesis:measurement-lineage",
                            "hypothesis:interface-method",
                        ),
                        "CONTROLLED_RERUN",
                        (
                            "Prepare a controlled rerun for a named owner; do not execute "
                            "automatically."
                        ),
                        first_ref,
                        {"external_write": True, "physical_action": True},
                        "PARTIAL",
                    ),
                ],
                "decision_analysis": {
                    "decision": "Choose the lowest-risk action that can change the decision.",
                    "criteria": [
                        {
                            "criterion_id": "decision:information-before-risk",
                            "name": "information before irreversible risk",
                            "mandatory": True,
                            "rationale": "Read-only and isolated checks precede protected work.",
                        }
                    ],
                    "evaluations": [],
                    "uncertainty": "Domain semantics and missing manifests remain explicit.",
                    "sensitivity": "A complete matching manifest may eliminate the rerun.",
                },
                "proposed_frontier": [
                    "action:evidence-request",
                    "action:sandbox-replay",
                ],
                "plan_revision_digest": context.input_head_set_digest,
            }
        else:
            raise AssertionError(f"unexpected generic model role: {request.role}")
        output = request.output_model.model_validate(payload)
        output_digest = domain_digest(
            "GENERIC_PROJECTPACK_MODEL_OUTPUT",
            "1.0.0",
            canonical_payload(output),
        )
        return ModelResult(
            output=output,
            model_id="GENERIC_PROJECTPACK_QA_MODEL",
            prompt_version=request.prompt_version,
            scripted=False,
            input_digest=context.input_head_set_digest,
            output_digest=output_digest,
        )

    @staticmethod
    def _hypothesis(
        object_id: str,
        hypothesis_id: str,
        statement: str,
        locus: str,
        evidence_ref: str | None,
        procedure: str,
    ) -> dict[str, object]:
        return {
            "hypothesis_id": hypothesis_id,
            "object_id": object_id,
            "statement": statement,
            "observed_problem": "The current result is not yet decision-ready.",
            "primary_locus": locus,
            "contributing_loci": [],
            "causal_depth": "UNDETERMINED",
            "scope_conditions": {"scope": "authorized sources at cutoff"},
            "support_evidence_refs": [] if evidence_ref is None else [evidence_ref],
            "counterevidence_refs": [],
            "missing_evidence": (
                ["authorized source that identifies the missing factor"]
                if evidence_ref is None
                else ["independent counterevidence"]
            ),
            "counterevidence_queries": [
                "Search for evidence that would distinguish or disconfirm this candidate."
            ],
            "assumptions": ["The selected sources describe the decision scope."],
            "uncertainty": "Domain authority remains reviewable and may require abstention.",
            "predicted_observations": [
                "The bounded check either identifies a material difference or narrows the gap."
            ],
            "discriminating_tests": [
                {
                    "test_id": f"test:{hypothesis_id.split(':')[-1]}",
                    "procedure_candidate": procedure,
                    "expected_if_true": "A material difference appears under the stated scope.",
                    "expected_if_alternative": "The check does not find that material difference.",
                    "risk_tier": "R0" if locus != "COMPONENT_INTERFACE_SYSTEM" else "R2",
                    "reversibility": "FULL",
                }
            ],
            "status": "TESTABLE",
        }

    @staticmethod
    def _action(
        object_id: str,
        action_id: str,
        hypothesis_ids: tuple[str, ...],
        family: str,
        specification: str,
        source_ref: str | None,
        effect_facts: dict[str, bool],
        reversibility: str,
    ) -> dict[str, object]:
        return {
            "action_id": action_id,
            "object_id": object_id,
            "hypothesis_ids": hypothesis_ids,
            "action_family": family,
            "specification": specification,
            "expected_information_value": "Changes the decision by resolving a named gap.",
            "estimated_seconds": 120,
            "reversibility": reversibility,
            "effect_facts": effect_facts,
            "effect_completeness_confirmed": True,
            "source_refs": [] if source_ref is None else [source_ref],
            "missing_evidence": [] if source_ref is not None else ["source evidence"],
        }


@pytest.mark.asyncio
async def test_four_packs_use_same_generic_model_without_oracle_or_cross_domain_leakage(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2] / "examples" / "projectpacks"
    names = (
        "public-demo-membrane",
        "6g-sandbox-hero",
        "sunrise-secondary",
        "l3pilot-regression",
        "opendreamkit-hidden-holdout",
    )
    results: dict[str, ProjectPackRunResult] = {}
    model = GenericProjectPackModel()
    for name in names:
        pack = load_project_pack(root / name)
        result = await run_project_pack(
            pack,
            workspace=tmp_path / name,
            model=model,
        )
        results[name] = result
        assert len(result.cycle.portfolio.hypotheses) == 3
        assert len(result.cycle.action_plan.alternatives) == 3
        assert result.cycle.commit.receipt.semantic_truth_certified is False
        assert all(span.cutoff_state.value == "ELIGIBLE" for span in result.selected_evidence)
        assert all(
            action.risk_tier.value != "R3" or action.state.value == "APPROVAL_PENDING"
            for action in result.cycle.action_plan.alternatives
        )
    assert (
        SufficiencyStatus.EXPERT_INPUT_REQUIRED
        in results["sunrise-secondary"].cycle.assessment.derived_status
    )
    odk_statements = " ".join(
        item.statement for item in results["opendreamkit-hidden-holdout"].cycle.portfolio.hypotheses
    ).lower()
    assert "vehicle" not in odk_statements
    assert "route clearance" not in odk_statements
    hidden_pack = load_project_pack(root / "opendreamkit-hidden-holdout")
    assert all(not source.path.lower().startswith("oracle/") for source in hidden_pack.sources)
    oracle_path = root / "opendreamkit-hidden-holdout" / "oracle" / "expected-invariants.json"
    assert oracle_path.is_file()
