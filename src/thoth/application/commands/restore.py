"""Public preview/apply and exact same-aggregate diff adapters."""

from pydantic import JsonValue

from thoth.application.commands.research_history import history_rpc_error
from thoth.application.services.research_history_scope import actor_scope_digest
from thoth.application.services.restore_planner import RestorePlanner
from thoth.application.services.restore_profiles import semantic_groups
from thoth.application.services.restore_publication import RestorePublication
from thoth.application.services.revision_diff import semantic_diff
from thoth.domain.research_history import (
    HistoryCapability,
    HistoryCoverage,
    HistoryDiff,
    HistoryDiffInput,
)
from thoth.domain.restore import (
    RestoreApplyInput,
    RestoreError,
    RestorePreview,
    RestorePreviewInput,
    RestoreSelection,
)
from thoth.protocol.deferred import current_operation
from thoth.protocol.registry import CommandHandler


class RestoreHandlers:
    def __init__(
        self, planner: RestorePlanner, publication: RestorePublication, legacy_diff: CommandHandler
    ) -> None:
        self.planner, self.publication, self.legacy_diff = planner, publication, legacy_diff

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        try:
            project = str(value.get("project_id", ""))
            self.planner.access.require_reads(project, ())
            selection = value.get("selection")
            if isinstance(selection, dict) and selection.get("project_id") != project:
                raise RestoreError("RESTORE_TARGET_MISMATCH")
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    def selection(self, value: dict[str, JsonValue]) -> RestoreSelection:
        if value.get("contract_version") == 2 or "selection" in value:
            request = RestorePreviewInput.model_validate(value)
            if request.project_id != request.selection.project_id:
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            return request.selection
        project, target = str(value["project_id"]), str(value["target_revision_digest"])
        revision, _ = self.planner.read_revision(project, target)
        if revision.entity_id != value.get("aggregate_id"):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        return RestoreSelection(
            project_id=project,
            entity_type=revision.entity_type,
            entity_id=revision.entity_id,
            target_revision_digest=target,
            expected_current_head=str(value["current_head_digest"]),
        )

    async def preview(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            selection = self.selection(value)
            try:
                with self.planner.ledger.transaction():
                    preview = self.planner.build(selection).preview
            except RestoreError as exc:
                preview = RestorePreview(
                    selection=selection,
                    availability="BLOCKED",
                    reason_codes=(exc.reason_code,),
                    capability=HistoryCapability(
                        restore="UNSUPPORTED"
                        if exc.reason_code == "RESTORE_SCHEMA_UNSUPPORTED"
                        else "READ_ONLY",
                        reason_codes=(exc.reason_code,),
                    ),
                    actor_scope_digest=actor_scope_digest(selection.project_id),
                )
            payload = preview.model_dump(mode="json")
            if value.get("contract_version") != 2 and "selection" not in value:
                payload.update(
                    {
                        "new_parent": selection.expected_current_head,
                        "restores_revision_digest": selection.target_revision_digest,
                        "dependent_impact": list(preview.impact.recalculate_refs),
                        "protected_boundary_warning": "external effects are not rolled back",
                        "change_set_candidate": preview.availability != "BLOCKED",
                    }
                )
                if preview.availability != "BLOCKED":
                    _, snapshot = self.planner.read_revision(
                        selection.project_id, selection.target_revision_digest
                    )
                    payload["proposed_restored_content"] = snapshot.content
            return payload
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def apply(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = RestoreApplyInput.model_validate(value)
            operation = current_operation.get()
            if operation is None:
                raise RestoreError("RESTORE_OPERATION_REQUIRED")
            return self.publication.apply(request, operation).model_dump(mode="json")
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def legacy_apply(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        from thoth.application.commands.revisions import RevisionRestoreInput

        try:
            old = RevisionRestoreInput.model_validate(value)
            target = self.planner.ledger.read_revision_by_id(
                old.project_id, old.selected_revision_id
            )
            if target is None:
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            selection = RestoreSelection(
                project_id=old.project_id,
                entity_type=old.entity_type,
                entity_id=old.entity_id,
                target_revision_digest=target.revision_digest,
                expected_current_head=old.expected_current_head,
            )
            with self.planner.ledger.transaction():
                plan = self.planner.build(selection)
                operation = current_operation.get()
                if operation is None or plan.preview.basis_digest is None:
                    raise RestoreError("RESTORE_OPERATION_REQUIRED")
                result = self.publication.apply(
                    RestoreApplyInput(
                        project_id=old.project_id,
                        selection=selection,
                        preview_basis_digest=plan.preview.basis_digest,
                        reason=old.reason,
                    ),
                    operation,
                    legacy=True,
                )
                return self.publication.result_payload(result, legacy=True)
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def diff(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = HistoryDiffInput.model_validate(
                {key: item for key, item in value.items() if key != "diff_profile_ref"}
            )
            with self.planner.ledger.transaction():
                left, before = self.planner.read_revision(
                    request.project_id, request.from_revision_digest
                )
                right, after = self.planner.read_revision(
                    request.project_id, request.to_revision_digest
                )
                if (left.entity_type, left.entity_id) != (right.entity_type, right.entity_id):
                    raise RestoreError("RESTORE_TARGET_MISMATCH")
                changes = semantic_diff(before.content, after.content)
                if value.get("contract_version") != 2:
                    result = await self.legacy_diff(value)
                    assert isinstance(result, dict)
                    return result
                return HistoryDiff(
                    from_revision_digest=left.revision_digest,
                    to_revision_digest=right.revision_digest,
                    diff=changes,
                    summary_groups=semantic_groups(changes),
                    coverage=HistoryCoverage(),
                ).model_dump(mode="json")
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc
