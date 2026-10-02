"""Atomically preserve completed outputs.

A stage is never replayed on its own. Only a user-requested "resume" (a new operation that names
the interrupted one) may reuse a completed stage, and only when this run's input basis for the
stage equals the stored one exactly; see research_stage_reuse.py.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from thoth.application.services.behavior_context import current_behavior_work
from thoth.application.services.request_records import RequestRecords
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import ResearchAttempt, RevisionRef
from thoth.domain.research_stage import ResearchStageRecord, ReusedStageOrigin


@dataclass(frozen=True)
class StageBases:
    basis: str
    reuse_basis: str
    schema: str
    behavior_basis: tuple[str, ...]


@dataclass(frozen=True)
class ReusedStage:
    """A completed stage of an interrupted operation whose input basis equals this run's."""

    record: ResearchStageRecord
    origin: ReusedStageOrigin


# Names a new run gives to the same content: the request revision it hangs under, the project head
# set read at its start and the id/time of the memory pack it rendered. They differ for every run
# and say nothing about what the stage was asked.
_RUN_IDENTITY_KEYS = frozenset(
    {
        "case_id",
        "input_head_set_digest",
        "request_ref",
        "request_revision_digest",
        "requirement_set_ref",
        "context_pack_id",
        "created_at",
    }
)


def _without_run_identity(value: object, request_ref: RevisionRef) -> object:
    """Drop this run's own names; the request's id and digest inside text read alike everywhere."""

    def plain(text: str) -> str:
        return text.replace(request_ref.revision_digest, "REQUEST").replace(
            request_ref.revision_id, "REQUEST"
        )

    if isinstance(value, str):
        return plain(value)
    if isinstance(value, dict):
        return {
            plain(key): _without_run_identity(item, request_ref)
            for key, item in cast(dict[str, object], value).items()
            if key not in _RUN_IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [_without_run_identity(item, request_ref) for item in cast(list[object], value)]
    return value


def _reuse_context(context: BaseModel, request_ref: RevisionRef) -> object:
    return _without_run_identity(json.loads(canonical_payload(context)), request_ref)


def stage_bases[T: BaseModel](work: ResearchWork, request: ModelRequest[T]) -> StageBases:
    context = request.context_pack
    behavior = current_behavior_work()
    behavior_basis = (
        () if behavior is None else tuple(s.snapshot_digest for s in behavior.snapshots)
    )
    schema = hashlib.sha256(
        json.dumps(
            request.output_model.model_json_schema(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    common = {
        "context": context,
        "role": request.role,
        "prompt": request.prompt_version,
        "schema": schema,
        "model_settings": work.model_settings,
        "cutoff": request.cutoff_at,
        "policy": request.model_policy_ref,
        "behavior_basis": behavior_basis,
    }
    return StageBases(
        basis=domain_digest(
            "ROLE_INPUT_BASIS",
            "2.0.0",
            canonical_payload({"request_ref": work.request_ref, **common}),
        ),
        reuse_basis=domain_digest(
            "ROLE_REUSE_BASIS",
            "1.0.0",
            canonical_payload({**common, "context": _reuse_context(context, work.request_ref)}),
        ),
        schema=schema,
        behavior_basis=behavior_basis,
    )


def persist_stage[T: BaseModel](
    records: RequestRecords,
    work: ResearchWork,
    request: ModelRequest[T],
    result: ModelResult[T] | None,
    elapsed_ms: int,
    reused: ReusedStage | None = None,
) -> RevisionRef:
    """Save one completed stage: a new model output, or a reuse of an interrupted run's output."""

    context = request.context_pack
    bases = stage_bases(work, request)
    if (result is None) == (reused is None):
        raise ValueError("STAGE_NEEDS_EXACTLY_ONE_SOURCE")
    if reused is not None:
        output = reused.record.output
        provider_input = reused.record.provider_input_digest
        provider_output = reused.record.provider_output_digest
        model_id, dispatch_ids = reused.record.model_id, ()
    else:
        assert result is not None
        output = request.output_model.model_validate(
            result.output.model_dump(mode="python")
        ).model_dump(mode="json")
        provider_input, provider_output = result.input_digest, result.output_digest
        model_id, dispatch_ids = result.model_id, result.dispatch_ids
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
            output_schema_digest=bases.schema,
            input_basis_digest=bases.basis,
            reuse_basis_digest=bases.reuse_basis,
            reused_from=None if reused is None else reused.origin,
            provider_input_digest=provider_input,
            provider_output_digest=provider_output,
            output_payload_digest=domain_digest(
                "RESEARCH_STAGE_OUTPUT", "2.0.0", canonical_payload(output)
            ),
            output=output,
            model_id=model_id,
            model_settings=work.model_settings,
            source_versions=tuple(sorted({s.source_version_id for s in context.evidence})),
            evidence_refs=tuple(s.span_id for s in context.evidence),
            behavior_basis=bases.behavior_basis,
            dispatch_ids=dispatch_ids,
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
                    "reused": reused is not None,
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
