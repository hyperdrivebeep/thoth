from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from thoth.adapters.models import ScriptedModel
from thoth.adapters.projectpacks.loader import source_path
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.reducers import SufficiencySignals
from thoth.application.services import (
    DecisionObjectService,
    IngestArtifactCommand,
    RevisionCommitService,
    compile_criterion,
    select_evidence_context,
)
from thoth.application.workflows.projectpack_run import ProjectPackRunResult
from thoth.application.workflows.thread_cycle import (
    ThreadCycleCommand,
)
from thoth.apps.projectpack_source_composition import create_pack_ingestion, create_pack_project
from thoth.apps.research_runtime import create_projectpack_research_cycle
from thoth.apps.storage_composition import open_stores
from thoth.domain.action import ActionCompilationPolicy
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.criterion import (
    CriterionCandidate,
    CriterionCompilationSignals,
    CriterionDraft,
)
from thoth.domain.enums import ActorKind, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.project import WorkThread
from thoth.domain.projectpack import LoadedProjectPack
from thoth.domain.projectpack_execution import ProjectPackExecutionRequest
from thoth.ports.model import ModelPort, ModelResolverPort
from thoth.ports.parser import ParserRegistryPort
from thoth.ports.projectpack import ProjectPackExecutionFactoryPort
from thoth.ports.store_bundle import StoreFactoryPort


async def run_project_pack(
    pack: LoadedProjectPack,
    *,
    workspace: Path,
    model: ModelPort | None = None,
    storage_factory: StoreFactoryPort | None = None,
    parser_registry: ParserRegistryPort | None = None,
) -> ProjectPackRunResult:
    scripted = model is None
    if scripted and pack.scripted_templates is None:
        raise ValueError("V0 run_project_pack requires explicitly enabled scripted fixtures")
    stores = open_stores(workspace, storage_factory)
    ledger = stores.ledger
    try:
        clock = SystemClock()
        ids = UuidIdGenerator()
        projects = stores.projects
        project = create_pack_project(pack, stores, clock)
        artifact_ledger, ingestion = create_pack_ingestion(
            pack, workspace, ledger, stores, project, projects, clock, ids, parser_registry
        )
        thread = WorkThread(
            thread_id=pack.scenario.thread_id,
            project_id=project.project_id,
            cycle_id=pack.scenario.cycle_id,
            problem=pack.scenario.problem,
            current_object_ids=(pack.scenario.object_id,),
            working_head_digest=head_set_digest({}),
        )
        stores.threads.create(thread)
        ingested = tuple(
            [
                await ingestion.ingest_async(
                    IngestArtifactCommand(
                        project_id=project.project_id,
                        source_uri=f"projectpack://{pack.project.pack_id}/{source.path}",
                        media_type=source.media_type,
                        raw=source_path(pack, source).read_bytes(),
                        authority=source.authority,
                        cutoff_state=source.cutoff_state,
                        security_class=source.security_class,
                        operation_id=ids.new("operation"),
                        version_label=source.version_label,
                        source_path=source_path(pack, source),
                    )
                )
                for source in pack.sources
            ]
        )
        evidence = tuple(span for result in ingested for span in result.evidence_candidates)
        if pack.policy.sealed_evidence_supported:
            evidence = tuple(
                span.model_copy(
                    update={
                        "support_state": SupportState.SUPPORTED,
                        "verification_state": VerificationState.PROVENANCE_VALID,
                    }
                )
                for span in evidence
            )
            for span in evidence:
                artifact_ledger.update_evidence(span)
        dependencies = stores.dependencies
        object_store = stores.decision_objects()
        object_service = DecisionObjectService(
            store=object_store,
            artifacts=artifact_ledger,
            dependencies=dependencies,
            ledger=ledger,
            commits=RevisionCommitService(
                ledger,
                clock,
                ids,
                policy_version=pack.policy.policy_version,
            ),
            clock=clock,
            ids=ids,
        )
        object_service.seed_profiles()
        _candidate, materialized, _commit = object_service.materialize(
            project_id=project.project_id,
            thread_id=thread.thread_id,
            purpose_statement=pack.scenario.problem,
            problem_frame=pack.scenario.problem,
            focus_refs=(f"PROJECTPACK:{pack.project.pack_id}",),
            trigger_evidence_refs=tuple(span.span_id for span in evidence),
            profile_refs=("GENERAL_RND_DECISION",),
            actor_ref="agent:projectpack-runner",
            object_id=pack.scenario.object_id,
            trigger_type="PROJECTPACK_SCENARIO",
        )
        if materialized is None:
            raise ValueError("ProjectPack DecisionObject materialization was held")
        current_head_digest = domain_digest(
            "WORKING_HEADS",
            "1.0.0",
            canonical_payload(ledger.read_heads(project.project_id)),
        )
        substitutions = {
            "$PROJECT_ID": project.project_id,
            "$OBJECT_ID": pack.scenario.object_id,
            "$HEAD_DIGEST": current_head_digest,
            **{f"$EVIDENCE_{index}": span.span_id for index, span in enumerate(evidence)},
        }
        criteria = tuple(
            _criterion_from_template(_substitute(value, substitutions), evidence)
            for value in pack.criteria_templates
        )
        selected_evidence = select_evidence_context(
            problem=pack.scenario.problem,
            evidence=evidence,
            forced_refs=tuple(
                dict.fromkeys(
                    reference for criterion in criteria for reference in criterion.evidence_refs
                )
            ),
        ).selected
        active_model = model
        if active_model is None:
            templates = _substitute(pack.scripted_templates, substitutions)
            template_map = _object(templates, "scripted fixture root")
            portfolio_template = _object(
                template_map.get("hypothesis_portfolio"), "hypothesis_portfolio"
            )
            action_template = _object(template_map.get("action_plan_draft"), "action_plan_draft")
            active_model = ScriptedModel(
                {
                    (
                        pack.scenario.case_id,
                        "HYPOTHESIS_GENERATOR",
                        "hypothesis_portfolio.v2",
                    ): portfolio_template,
                    (
                        pack.scenario.case_id,
                        "ACTION_PLANNER",
                        "action_alternatives.v2",
                    ): action_template,
                }
            )
        cycle = await create_projectpack_research_cycle(
            stores=stores,
            ledger=ledger,
            objects=object_store,
            artifacts=artifact_ledger,
            model=active_model,
            clock=clock,
            ids=ids,
            dependencies=dependencies,
            policy_version=pack.policy.policy_version,
        ).execute(
            ThreadCycleCommand(
                case_id=pack.scenario.case_id,
                project_id=project.project_id,
                thread_id=thread.thread_id,
                object_id=pack.scenario.object_id,
                problem=pack.scenario.problem,
                cutoff_at=pack.project.cutoff_at,
                criteria=criteria,
                evidence=selected_evidence,
                sufficiency_signals=SufficiencySignals.model_validate(
                    pack.policy.sufficiency_signals
                ),
                action_policy=ActionCompilationPolicy(
                    minimum_tier_by_family=pack.policy.minimum_action_tier_by_family,
                    approver_role_by_family=pack.policy.approver_role_by_family,
                    unknown_family_tier=pack.policy.unknown_action_family_tier,
                ),
                actor=ActorRef(
                    actor_id="agent:projectpack-runner",
                    kind=ActorKind.AGENT,
                    role="cycle-runner",
                ),
                policy_version=pack.policy.policy_version,
                model_policy_ref=pack.policy.model_policy_ref,
            )
        )
        return ProjectPackRunResult(
            pack_id=pack.project.pack_id,
            project=project,
            thread=thread,
            artifact_ids=tuple(result.document.artifact.artifact_id for result in ingested),
            evidence=evidence,
            evidence_count=len(evidence),
            selected_evidence=selected_evidence,
            selected_evidence_count=len(selected_evidence),
            scripted_model=scripted,
            cycle=cycle,
        )
    finally:
        stores.close()


class ProjectPackExecutionFactory(ProjectPackExecutionFactoryPort):
    def __init__(
        self,
        *,
        workspace: Path,
        models: ModelResolverPort,
        storage_factory: StoreFactoryPort | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._models = models
        self._storage_factory = storage_factory

    async def execute(
        self,
        pack: LoadedProjectPack,
        request: ProjectPackExecutionRequest,
    ) -> dict[str, JsonValue]:
        model = (
            None
            if request.scripted
            else self._models.resolve(provider=request.provider, model=request.model)
        )
        result = await run_project_pack(
            pack,
            workspace=self._workspace,
            model=model,
            storage_factory=self._storage_factory,
        )
        return cast(dict[str, JsonValue], result.model_dump(mode="json"))


def _substitute(value: object, substitutions: dict[str, str]) -> object:
    if isinstance(value, str):
        return substitutions.get(value, value)
    if isinstance(value, list):
        values = cast(list[object], value)
        return [_substitute(child, substitutions) for child in values]
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _substitute(child, substitutions) for key, child in mapping.items()}
    return value


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    mapping = cast(dict[object, object], value)
    return {str(key): child for key, child in mapping.items()}


def _criterion_from_template(
    value: object, evidence: tuple[EvidenceSpan, ...]
) -> CriterionCandidate:
    template = _object(value, "criterion template")
    draft_value = template.get("draft")
    signals_value = template.get("signals")
    if draft_value is None or signals_value is None:
        raise ValueError("criterion template requires draft and signals")
    result = compile_criterion(
        CriterionDraft.model_validate(draft_value),
        evidence=evidence,
        signals=CriterionCompilationSignals.model_validate(signals_value),
    )
    if result.candidate is None:
        raise ValueError(f"criterion template was rejected: {result.status.value}")
    return result.candidate
