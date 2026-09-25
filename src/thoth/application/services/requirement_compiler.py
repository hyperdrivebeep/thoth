"""Versioned initial task profiles. Model proposals cannot erase governing rules."""

from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.evidence_requirements import (
    EvidenceRequirement,
    RequirementProposal,
    RequirementSetRevision,
)
from thoth.domain.research_request import RevisionRef
from thoth.ports.task_profile import TaskProfileCatalogPort


def compile_requirements(
    request_ref: RevisionRef,
    question: str,
    proposal: RequirementProposal,
    contracts: tuple[CriterionContractRecord, ...],
    run: str,
    *,
    profiles: TaskProfileCatalogPort,
) -> RequirementSetRevision:
    available = {p.profile_ref: p for p in profiles.profiles() if p.enabled and p.authority_ref}
    candidates = tuple(dict.fromkeys(proposal.profile_candidates))
    decided = len(candidates) == 1 and candidates[0] in available
    profile = candidates[0] if decided else "PROFILE_DECISION_REQUIRED"
    rules = {f"request:{request_ref.revision_digest}": ("answer", question)}
    if decided:
        rules.update(
            {
                f"profile:{profile}:{rule.rule_id}": (rule.target, rule.question)
                for rule in available[profile].rules
            }
        )
    for contract in contracts:
        if not is_governing_contract(contract, request_ref.project_id):
            continue
        for field in contract.required_evidence:
            rules[f"criterion:{contract.revision_digest}:{field}"] = ("criterion", field)
    requirements = [
        EvidenceRequirement(
            requirement_id=f"bound:{i}",
            kind="BOUND_OBLIGATION",
            target=target,
            rule_refs=(rule,),
            question=value,
            rationale=f"Required by current {rule}",
            needed_for=target,
            blocker=f"{target}:HOLD",
            followup=f"Resolve {value}",
            counterevidence_required=target == "counterevidence",
        )
        for i, (rule, (target, value)) in enumerate(rules.items())
    ]
    for i, candidate in enumerate(proposal.checks):
        # Only governing rules above may create BOUND effects. Unmapped model checks
        # remain research suggestions even if a model asks for a policy exemption.
        requirements.append(
            candidate.model_copy(
                update={
                    "requirement_id": f"check:{i}",
                    "kind": "RESEARCH_CHECK",
                    "rule_refs": (),
                    "blocker": "RESEARCH_GAP",
                }
            )
        )
    for i, requirement in enumerate(requirements):
        governing = next(
            (
                c
                for c in contracts
                if any(c.revision_digest in ref for ref in requirement.rule_refs)
            ),
            None,
        )
        if governing is not None:
            requirements[i] = requirement.model_copy(
                update={
                    "governing_context": {
                        "verification_spec": governing.verification_spec,
                        "context_spec": governing.context_spec,
                        "governance": governing.governance,
                        "field_authority": governing.field_authority_and_version,
                        "usage_authorization": governing.usage_authorization,
                    }
                }
            )
    return RequirementSetRevision(
        request_ref=request_ref,
        profile_ref=profile,
        requirements=tuple(requirements),
        mandatory_rule_coverage={
            rule: requirements[i].requirement_id for i, rule in enumerate(rules)
        },
        unresolved_profile_choices=() if decided else candidates or ("UNKNOWN",),
        generation_run=run,
    )


def is_governing_contract(contract: CriterionContractRecord, project_id: str) -> bool:
    if contract.project_id != project_id or contract.invalidation_state != "CURRENT":
        return False
    if contract.lifecycle == "DRAFT" or contract.consistency != "CONSISTENT":
        return False
    source_bound = any(
        meta.get("authority") in {"OFFICIAL", "APPROVED"}
        and meta.get("applicability") in {"APPLICABLE", "VERIFIED"}
        for meta in contract.field_authority_and_version.values()
    )
    return source_bound or contract.usage_authorization == "AUTHORIZED_EVALUATOR_INPUT"
