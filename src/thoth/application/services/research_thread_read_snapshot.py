"""Scoped snapshot reads for the v2 thread view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from thoth.application.services.historical_access_verification import (
    require_historical_manifest_access,
    require_historical_operation_access,
)
from thoth.application.services.research_freshness import (
    ResearchFreshnessService,
    boundary_currentness,
)
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.application.services.user_activity_projection import source_activity_contexts
from thoth.domain.enums import EntityType
from thoth.domain.evidence import connected_retrieval_spans
from thoth.domain.model_dispatch import ModelDispatchRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_codec import decode_current_result_manifest
from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    ResearchAttempt,
    ResearchBudget,
    RevisionRef,
    ThreadRequestRevision,
)

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


@dataclass(frozen=True)
class ResearchThreadReadSnapshot:
    current_ref: RevisionRef
    request: ThreadRequestRevision
    attempt: ResearchAttempt | None
    result_ref: RevisionRef | None
    manifest: CurrentResultManifest | CurrentResultManifestV21 | None
    currentness: BasisCurrentness
    fresh: bool
    budget: ResearchBudget | None
    dispatches: tuple[ModelDispatchRecord, ...]
    activity_source_context: dict[str, dict[str, object]]
    operation: OperationRecord | None


def read_research_snapshot(
    host: ResearchThreadHandlers, project_id: str, thread_id: str
) -> ResearchThreadReadSnapshot | None:
    with host.records.ledger.transaction(), scope_read_transaction():
        current = host.records.read(project_id, EntityType.THREAD, f"request:{thread_id}")
        if current is None:
            return None
        request = ThreadRequestRevision.model_validate(current[1])
        attempt = host.records.journal_read(project_id, request.operation_id, ResearchAttempt)
        result = host.records.read(
            project_id, EntityType.DECISION_OBJECT, f"result:{thread_id}"
        )
        manifest = None if result is None else decode_current_result_manifest(result[1])
        currentness = ResearchFreshnessService(
            host.records.ledger,
            host.projects,
            host.governance,
            host.analysis.artifacts,
            host.operations,
        ).evaluate_result(
            project_id,
            manifest.research_basis if isinstance(manifest, CurrentResultManifestV21) else None,
            operation_id=None if manifest is None else manifest.operation_id,
            completion=None if manifest is None else manifest.completion,
        )
        fresh = manifest is not None and manifest.request_ref == current[0]
        project, policy = host.projects.read(project_id), host.governance.read_policy(
            project_id
        )
        fresh = (
            fresh
            and project is not None
            and policy is not None
            and (
                project.cutoff_at.isoformat() == request.cutoff_at
                and project.policy_binding_ref == request.policy_ref
                and policy.policy_digest == request.policy_digest
            )
        )
        op = host.operations.read(request.operation_id)
        if op is not None:
            require_historical_operation_access(host.access, op)
        if result is not None:
            host.access.require_reads(project_id, (f"revision:{result[0].revision_digest}",))
        if manifest is not None and result is not None:
            require_historical_manifest_access(
                host.access,
                host.records.ledger,
                host.operations,
                result[0],
                manifest,
                thread_id,
            )
        visible_evidence = ()
        if manifest is not None and fresh:
            # Source bindings/cutoff can change without a new user input.
            visible_evidence = host.analysis.evidence(project_id)
            present = {
                s.span_id: f"{s.source_version_id}:{s.text_sha256}"
                for s in connected_retrieval_spans(visible_evidence)
            }
            fresh = set(manifest.source_refs) <= present.keys() and all(
                present.get(key) == digest for key, digest in manifest.source_basis.items()
            )
            selected = tuple(
                s
                for s in visible_evidence
                if s.span_id in manifest.source_refs
            )
            if manifest.source_context_digest is not None:
                fresh = fresh and manifest.source_context_digest == host.analysis.source_digest(
                    selected, version=manifest.source_context_version or "2.0.0"
                )
        currentness = boundary_currentness(currentness, fresh)
        fresh = fresh and currentness.state == "CURRENT"
        budget = host.records.journal_read(project_id, f"budget:{thread_id}", ResearchBudget)
        dispatches: list[ModelDispatchRecord] = []
        try:
            listed = host.records.controls.list(
                project_id, "RESEARCH_EXECUTION", "ModelDispatchRecord", latest_only=True
            )
        except ValidationError:
            listed = ()
        for record in listed:
            if record.payload.get("thread_id") != thread_id:
                continue
            try:
                dispatches.append(ModelDispatchRecord.model_validate(record.payload))
            except ValidationError:
                continue
        activity_span_ids = set(() if manifest is None else manifest.source_refs)
        if attempt is not None:
            focus = attempt.draft_progress.get("evidence_focus")
            if isinstance(focus, dict):
                focus_record = cast(dict[str, object], focus)
                locators = focus_record.get("locators")
                if isinstance(locators, list):
                    for item in cast(list[object], locators):
                        if not isinstance(item, dict):
                            continue
                        locator = cast(dict[str, object], item)
                        span_id = locator.get("span_id")
                        if isinstance(span_id, str):
                            activity_span_ids.add(span_id)
        if activity_span_ids and not visible_evidence:
            visible_evidence = host.analysis.evidence(project_id)
        activity_source_context = source_activity_contexts(
            (
                span
                for span in visible_evidence
                if span.span_id in activity_span_ids
            ),
            host.analysis.artifacts,
        )
    return ResearchThreadReadSnapshot(
        current_ref=current[0],
        request=request,
        attempt=attempt,
        result_ref=None if result is None else result[0],
        manifest=manifest,
        currentness=currentness,
        fresh=fresh,
        budget=budget,
        dispatches=tuple(dispatches),
        activity_source_context=activity_source_context,
        operation=op,
    )
