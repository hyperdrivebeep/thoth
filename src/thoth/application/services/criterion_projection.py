"""A read-only CriterionCandidate view never replaces its full owner."""

from decimal import Decimal

from thoth.domain.criterion import CriterionCandidate, FormulaComputation, ThresholdRule
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import AuthorityState


def criterion_projection(contract: CriterionContractRecord) -> CriterionCandidate:
    computation = None
    if contract.computation_spec is not None:
        expression = contract.computation_spec.get("expression")
        if isinstance(expression, str):
            computation = FormulaComputation(
                expression=expression,
                unit=str(contract.computation_spec.get("unit", "UNRESOLVED")),
            )
    acceptance = None
    if contract.acceptance_rule is not None:
        target = contract.acceptance_rule.get("target")
        acceptance = ThresholdRule(
            operator=str(contract.acceptance_rule.get("operator", "UNRESOLVED")),
            target=None if target is None else Decimal(str(target)),
        )
    authority_values = {
        value.get("authority") for value in contract.field_authority_and_version.values()
    }
    authority = (
        AuthorityState.OFFICIAL
        if authority_values and authority_values <= {"OFFICIAL", "APPROVED"}
        else AuthorityState.UNCLASSIFIED
    )
    return CriterionCandidate(
        criterion_id=contract.criterion_id,
        source_contract_revision=contract.revision_digest,
        project_id=contract.project_id,
        name=contract.identity.get("name", contract.criterion_id),
        measured_construct=contract.construct_outcome_definition,
        computation=computation,
        context=contract.context_spec,
        acceptance_rule=acceptance,
        evidence_refs=tuple(contract.required_evidence),
        authority_state=authority,
        reference_candidate=False,
        evaluator_input_allowed=(contract.usage_authorization == "AUTHORIZED_EVALUATOR_INPUT"),
    )
