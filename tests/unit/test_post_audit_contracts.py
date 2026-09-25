"""Expected behavior for the first audit's serializer and context defects."""

from datetime import UTC, datetime
from typing import Any, cast

from tests.unit.domain.test_models import SHA

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.task_profiles import default_task_profiles
from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.requirement_compiler import compile_requirements
from thoth.application.services.research_retrieval import adjacent_packet, lexical_candidates
from thoth.domain.artifact import SourceLocator
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evaluation_run import sealed_payload
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import RequirementProposal, ResearchSourcePlan
from thoth.domain.research_request import RevisionRef
from thoth.domain.task_profile import TaskProfileRecord, TaskProfileRule


def span(identifier: str, line: int, text: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="p",
        artifact_id="a",
        source_version_id="v",
        locator=SourceLocator(line=line),
        exact_text=text,
        text_sha256=SHA,
        extraction_method="text",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.INFORMAL,
        verification_state=VerificationState.NOT_CHECKED,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def test_packet_preserves_each_canonical_span_once() -> None:
    source = (
        span("header", 1, "단위 ms"),
        span("row", 2, "alpha 12"),
        span("footnote", 3, "조건부"),
    )
    packet = adjacent_packet(tuple(s.span_id for s in source), source, source, "alpha")
    assert len(packet) == len({s.span_id for s in packet}) == 3


def test_source_selector_can_be_expressed_in_actual_provider_schema() -> None:
    schema = cast(dict[str, Any], strict_output_schema(ResearchSourcePlan))
    item = schema["properties"]["selectors"]["items"]
    if "$ref" in item:
        item = schema["$defs"][item["$ref"].split("/")[-1]]
    assert {"connector_id", "selector"} <= item["properties"].keys()


def test_legacy_selector_decodes_and_emits_closed_typed_arguments() -> None:
    plan = ResearchSourcePlan.model_validate(
        {
            "reason": "query",
            "selectors": [
                {"connector_id": "registered", "selector": {"relative_path": "report.html"}}
            ],
        }
    )
    dumped = plan.model_dump(mode="json")
    assert dumped["selectors"][0]["selector"] == [{"name": "relative_path", "value": "report.html"}]


def ref() -> RevisionRef:
    return RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="r",
        revision_digest=SHA,
    )


def test_fourth_profile_registration_is_consumed_without_a_compiler_branch() -> None:
    catalog = default_task_profiles()
    catalog.register(
        TaskProfileRecord(
            profile_ref="REGISTERED_FOURTH:2",
            version=2,
            authority_ref="approved-task-contract",
            rules=(
                TaskProfileRule(
                    rule_id="target", target="answer", question="Verify the requested field"
                ),
            ),
        )
    )
    compiled = compile_requirements(
        ref(),
        "question",
        RequirementProposal(profile_candidates=("REGISTERED_FOURTH:2",)),
        (),
        "run",
        profiles=catalog,
    )
    assert compiled.profile_ref == "REGISTERED_FOURTH:2"
    assert not compiled.unresolved_profile_choices
    assert "profile:REGISTERED_FOURTH:2:target" in compiled.mandatory_rule_coverage


def test_unapproved_draft_is_not_a_bound_obligation() -> None:
    draft = CriterionContractRecord(
        criterion_revision_id="c1",
        criterion_id="c",
        project_id="p",
        identity={},
        profile_refs=(),
        construct_outcome_definition="draft",
        verification_spec={},
        required_evidence=("unapproved field",),
        field_evidence_map={},
        field_authority_and_version={},
        governance={},
        revision_digest="b" * 64,
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    compiled = compile_requirements(
        ref(),
        "question",
        RequirementProposal(profile_candidates=("DOCUMENT_QUESTION:1",)),
        (draft,),
        "run",
        profiles=default_task_profiles(),
    )
    assert not any(r.startswith("criterion:") for r in compiled.mandatory_rule_coverage)


def test_active_n06_limits_and_use_receipts_cover_new_retrieval_stages() -> None:
    snapshot = ActiveBehaviorSnapshot.model_validate(
        sealed_payload(
            "ACTIVE_BEHAVIOR_SNAPSHOT",
            "snapshot_digest",
            {
                "project_id": "p",
                "component": "RETRIEVAL_POLICY",
                "environment": "LOCAL",
                "artifact_ref": None,
                "content_digest": SHA,
                "policy": RetrievalBehaviorPolicy(max_spans=1, character_budget=100),
                "origin": "BUILTIN_DEFAULT",
                "registry_revision": 0,
                "exposure_ref": None,
                "ignored_registry_digest": None,
                "reason_code": None,
            },
        )
    )
    context = BehaviorWorkContext("p", (snapshot,))
    source = (span("header", 1, "단위 ms"), span("row", 2, "alpha 12"), span("foot", 3, "반증"))
    with behavior_work_scope(context):
        candidates = lexical_candidates("alpha", (), source)
        packet = adjacent_packet(tuple(s.span_id for s in candidates), candidates, source, "alpha")
    assert len(candidates) <= 1 and len(packet) <= 1
    assert len(context.uses) == 2
    assert all(u.component == BehaviorArtifactKind.RETRIEVAL_POLICY for u in context.uses)
