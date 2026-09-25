"""Atomically preserve completed outputs; automatic stage replay is intentionally unsupported."""

import hashlib
import json

from pydantic import BaseModel

from thoth.application.services.behavior_context import current_behavior_work
from thoth.application.services.request_records import RequestRecords
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import ResearchAttempt, RevisionRef
from thoth.domain.research_stage import ResearchStageRecord


def persist_stage[T: BaseModel](
    records: RequestRecords,
    work: ResearchWork,
    request: ModelRequest[T],
    result: ModelResult[T],
    elapsed_ms: int,
) -> RevisionRef:
    context = request.context_pack
    behavior = current_behavior_work()
    behavior_basis = (
        () if behavior is None else tuple(s.snapshot_digest for s in behavior.snapshots)
    )
    output = request.output_model.model_validate(
        result.output.model_dump(mode="python")
    ).model_dump(mode="json")
    schema = hashlib.sha256(
        json.dumps(
            request.output_model.model_json_schema(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    basis = domain_digest(
        "ROLE_INPUT_BASIS",
        "2.0.0",
        canonical_payload(
            {
                "request_ref": work.request_ref,
                "context": context,
                "role": request.role,
                "prompt": request.prompt_version,
                "schema": schema,
                "model_settings": work.model_settings,
                "cutoff": request.cutoff_at,
                "policy": request.model_policy_ref,
                "behavior_basis": behavior_basis,
            }
        ),
    )
    with records.ledger.transaction():
        work.boundary.check()
        current = records.read(request.project_id, EntityType.THREAD, work.request_ref.entity_id)
        if current is None or current[0] != work.request_ref:
            raise ValueError("STAGE_REQUEST_BASIS_CHANGED")
        operation_id = str(current[1]["operation_id"])
        attempt = records.journal_read(request.project_id, operation_id, ResearchAttempt)
        if attempt is None:
            raise ValueError("STAGE_ATTEMPT_MISSING")
        record = ResearchStageRecord(
            request_ref=work.request_ref,
            operation_id=operation_id,
            role=request.role.value,
            prompt_version=request.prompt_version,
            output_codec=request.output_model.__name__,
            output_schema_digest=schema,
            input_basis_digest=basis,
            provider_input_digest=result.input_digest,
            provider_output_digest=result.output_digest,
            output_payload_digest=domain_digest(
                "RESEARCH_STAGE_OUTPUT", "2.0.0", canonical_payload(output)
            ),
            output=output,
            model_id=result.model_id,
            model_settings=work.model_settings,
            source_versions=tuple(sorted({s.source_version_id for s in context.evidence})),
            evidence_refs=tuple(s.span_id for s in context.evidence),
            behavior_basis=behavior_basis,
            dispatch_ids=result.dispatch_ids,
            context_bytes=len(canonical_payload(context)),
            elapsed_ms=elapsed_ms,
            completed_at=records.clock.now(),
        )
        ref = records.save(
            request.project_id,
            EntityType.DECISION_OBJECT,
            records.ids.new("research-stage"),
            record,
            evidence_refs=record.evidence_refs,
        )
        records.journal(
            request.project_id,
            operation_id,
            attempt.model_copy(
                update={"completed_stage_refs": (*attempt.completed_stage_refs, ref)}
            ),
        )
        if records.events is not None:
            records.events.append(
                project_id=request.project_id,
                operation_id=operation_id,
                event_type="research.stage.completed",
                payload={
                    "role": request.role.value,
                    "stage_ref": ref.model_dump(mode="json"),
                    "context_bytes": record.context_bytes,
                    "elapsed_ms": elapsed_ms,
                    "dispatch_ids": list(record.dispatch_ids),
                },
            )
    work.record_refs.append(ref)
    return ref


def read_stage(records: RequestRecords, ref: RevisionRef) -> ResearchStageRecord:
    revision = records.ledger.read_revision_by_digest(ref.project_id, ref.revision_digest)
    snapshot = None if revision is None else records.ledger.read_snapshot(revision.snapshot_id)
    if (
        revision is None
        or snapshot is None
        or (revision.entity_id, revision.entity_type.value) != (ref.entity_id, ref.entity_type)
    ):
        raise ValueError("STAGE_REVISION_BINDING_MISMATCH")
    return ResearchStageRecord.model_validate(snapshot.content)
