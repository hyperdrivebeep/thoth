"""Restore proposals keep their preview basis through validation and strict apply."""

from typing import cast

from pydantic import JsonValue, TypeAdapter

from thoth.application.commands.research_history import history_rpc_error
from thoth.application.commands.restore import RestoreHandlers
from thoth.application.commands.revisions_full import (
    ChangeSetCommitInput,
    ChangeSetValidateInput,
    RestoreProposeInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.restore import (
    RestoreApplyInput,
    RestoreApplyResult,
    RestoreError,
    RestoreProposalV1,
)
from thoth.protocol.deferred import current_operation
from thoth.protocol.registry import CommandHandler


class RestoreProposalHandlers:
    def __init__(
        self, restore: RestoreHandlers, validate: CommandHandler, commit: CommandHandler
    ) -> None:
        self.restore, self.legacy_validate, self.legacy_commit = restore, validate, commit

    async def propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = RestoreProposeInput.model_validate(value)
            selection = self.restore.selection(
                {
                    key: item
                    for key, item in value.items()
                    if key
                    in {
                        "project_id",
                        "aggregate_id",
                        "target_revision_digest",
                        "current_head_digest",
                    }
                }
            )
            with self.restore.planner.ledger.transaction():
                plan = self.restore.planner.build(selection)
                payload: dict[str, object] = {
                    "record_kind": "RestoreProposal",
                    "schema_version": "1.0.0",
                    "selection": selection.model_dump(mode="json"),
                    "preview_basis_digest": plan.preview.basis_digest,
                    "reason": request.reason,
                    "profile_id": plan.basis.profile_id,
                    "impact": plan.basis.impact.model_dump(mode="json"),
                    "restores_revision_digest": selection.target_revision_digest,
                    "parent_revision_digests": (
                        selection.expected_current_head,
                        selection.target_revision_digest,
                    ),
                }
                proposal = self.restore.publication.controls.create(
                    project_id=request.project_id,
                    namespace="REVISION",
                    record_type="PROPOSAL",
                    state="DRAFT",
                    payload=RestoreProposalV1.model_validate(payload).model_dump(mode="python"),
                )
                change = self.restore.publication.controls.create(
                    project_id=request.project_id,
                    namespace="REVISION",
                    record_type="CHANGE_SET",
                    state="STAGED",
                    payload={
                        "expected_head_set": plan.basis.expected_heads,
                        "candidate_revision_digests": (proposal.record_digest,),
                        "transition_reason": request.reason,
                        "parent_revision_digests": (
                            selection.expected_current_head,
                            selection.target_revision_digest,
                        ),
                    },
                )
            return {
                "restore_proposal": proposal.model_dump(mode="json"),
                "change_set": change.model_dump(mode="json"),
                "impact_plan": plan.basis.impact.model_dump(mode="json"),
                "external_effect_rollback_claimed": False,
            }
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    def _read(self, project: str, identifier: str):
        controls = self.restore.publication.controls
        change = controls.read(project, "REVISION", identifier)
        if change is None:
            raise RestoreError("RESTORE_PROPOSAL_UNAVAILABLE")
        digests = change.payload.get("candidate_revision_digests", ())
        if not isinstance(digests, (list, tuple)):
            raise RestoreError("RESTORE_PROPOSAL_UNAVAILABLE")
        records = [
            controls.read_digest(project, str(digest))
            for digest in cast(list[object] | tuple[object, ...], digests)
        ]
        restores = [
            record
            for record in records
            if record is not None and record.payload.get("restores_revision_digest")
        ]
        if not restores:
            return change, None
        if len(restores) != 1 or len(records) != 1:
            raise RestoreError("RESTORE_MIXED_CHANGESET_UNSUPPORTED")
        if restores[0].payload.get("record_kind") != "RestoreProposal":
            raise RestoreError("RESTORE_PREVIEW_STALE")
        return change, restores[0]

    def _request(self, project: str, proposal: ControlRecord) -> RestoreApplyInput:
        return RestoreApplyInput.model_validate(
            {
                "project_id": project,
                **{
                    key: proposal.payload[key]
                    for key in ("selection", "preview_basis_digest", "reason")
                },
            }
        )

    async def validate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = ChangeSetValidateInput.model_validate(value)
            with self.restore.planner.ledger.transaction():
                current, proposal = self._read(request.project_id, request.record_id)
                if proposal is None:
                    result = await self.legacy_validate(value)
                    assert isinstance(result, dict)
                    return result
                if current.version != request.expected_change_set_revision:
                    raise RestoreError("RESTORE_PREVIEW_STALE")
                apply_input = self._request(request.project_id, proposal)
                plan = self.restore.planner.build(apply_input.selection)
                if plan.preview.basis_digest != apply_input.preview_basis_digest:
                    raise RestoreError("RESTORE_PREVIEW_STALE")
                bundle = {
                    "basis_digest": plan.preview.basis_digest,
                    "impact_propagation_plan": plan.basis.impact.model_dump(mode="json"),
                }
                digest = domain_digest("RESTORE_VALIDATION", "1.0.0", canonical_payload(bundle))
                changed = self.restore.publication.controls.create(
                    project_id=request.project_id,
                    namespace="REVISION",
                    record_type="CHANGE_SET",
                    record_id=current.record_id,
                    state="READY_TO_COMMIT",
                    payload={
                        **current.payload,
                        "validation_bundle": bundle,
                        "validation_bundle_digest": digest,
                    },
                )
                return {
                    "change_set": changed.model_dump(mode="json"),
                    "state": changed.state,
                    "validation_bundle_digest": digest,
                    "impact_propagation_plan": bundle["impact_propagation_plan"],
                }
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def commit(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = ChangeSetCommitInput.model_validate(value)
            with self.restore.planner.ledger.transaction():
                current, proposal = self._read(request.project_id, request.record_id)
                if proposal is None:
                    result = await self.legacy_commit(value)
                    assert isinstance(result, dict)
                    return result
                if (
                    current.state != "READY_TO_COMMIT"
                    or current.payload.get("validation_bundle_digest")
                    != request.validation_bundle_digest
                ):
                    raise RestoreError("RESTORE_PREVIEW_STALE")
                if (
                    head_set_digest(
                        TypeAdapter(dict[str, str]).validate_python(
                            current.payload["expected_head_set"]
                        )
                    )
                    != request.expected_head_set_digest
                ):
                    raise RestoreError("RESTORE_HEAD_CHANGED")
                operation = current_operation.get()
                if operation is None:
                    raise RestoreError("RESTORE_OPERATION_REQUIRED")
                self.restore.publication.apply(
                    self._request(request.project_id, proposal),
                    operation,
                    publication_view=lambda result: self._publication_view(current, result),
                )
                stored = self.restore.publication.operations.read(operation.operation_id)
                assert stored is not None and stored.result is not None
                return stored.result
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    def _publication_view(
        self, current: ControlRecord, result: RestoreApplyResult
    ) -> dict[str, JsonValue]:
        revised = self.restore.publication.controls.create(
            project_id=current.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            record_id=current.record_id,
            state="COMMITTED",
            payload={**current.payload, "restore_result": result.model_dump(mode="json")},
        )
        receipt = next(
            (
                receipt
                for receipt in self.restore.planner.ledger.read_receipts(current.project_id)
                if receipt.receipt_id == result.receipt_id
            ),
            None,
        )
        return {
            **result.model_dump(mode="json"),
            "change_set": revised.model_dump(mode="json"),
            "atomic_commit": True,
            "contains_restore": result.status == "APPLIED",
            "receipt": None if receipt is None else receipt.model_dump(mode="json"),
            "branch_revision_digests": [],
            "stale_recompute_set": list(result.impact.recalculate_refs),
        }
