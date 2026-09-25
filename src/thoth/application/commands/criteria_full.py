from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class CriterionListInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    lifecycle: str | None = Field(default=None, max_length=40)
    profile_ref: str | None = Field(default=None, max_length=160)
    completeness: str | None = Field(default=None, max_length=40)
    consistency: str | None = Field(default=None, max_length=40)


class CriterionReadInput(ProjectInput):
    criterion_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ProfileListInput(ProjectInput):
    domain_hint: str | None = Field(default=None, max_length=160)
    enabled_only: bool = True


class ProfileReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class ReferenceListInput(ProjectInput):
    criterion_id: str | None = Field(default=None, max_length=160)


class ReferenceReadInput(ProjectInput):
    reference_candidate_id: str = Field(min_length=1, max_length=160)


class ConflictListInput(ProjectInput):
    criterion_id: str | None = Field(default=None, max_length=160)


class ConflictReadInput(ProjectInput):
    conflict_id: str = Field(min_length=1, max_length=160)


class AuditReadInput(CriterionReadInput):
    pass


class CompileInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    source_span_ids: tuple[str, ...]
    goal_requirement_refs: tuple[str, ...] = ()
    profile_refs: tuple[str, ...] = ()


class RevisionBoundInput(CriterionReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class FieldCorrectInput(RevisionBoundInput):
    field_path: str = Field(min_length=1, max_length=260)
    proposed_value: JsonValue
    evidence_span_ids: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class ProfileApplyInput(RevisionBoundInput):
    profile_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class RevalidateInput(CriterionReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class RecalculateInput(CriterionReadInput):
    variables: dict[str, Decimal]
    input_evidence_refs: tuple[str, ...]
    evaluator_binding_digest: str = Field(min_length=64, max_length=64)


class ReferenceGenerateInput(RevisionBoundInput):
    investigation_id: str | None = Field(default=None, max_length=160)
    source_scope: tuple[str, ...]
    scenarios: tuple[str, ...]


class ChangeProposeInput(RevisionBoundInput):
    change_type: str = Field(pattern=r"^(TARGET|METHOD|DECISION_RULE|EVALUATOR_BINDING|PROFILE)$")
    proposed_patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)


class CriterionFullHandlers:
    def __init__(
        self,
        *,
        store: CriterionContractStorePort,
        service: CriterionContractService,
    ) -> None:
        self._store = store
        self._service = service

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CriterionListInput.model_validate(value)
        items = self._store.list_contracts(request.project_id)
        filtered = tuple(
            item
            for item in items
            if (request.thread_id is None or item.thread_id == request.thread_id)
            and (request.lifecycle is None or item.lifecycle == request.lifecycle)
            and (request.profile_ref is None or request.profile_ref in item.profile_refs)
            and (request.completeness is None or item.completeness == request.completeness)
            and (request.consistency is None or item.consistency == request.consistency)
        )
        return {"criteria": [item.model_dump(mode="json") for item in filtered]}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CriterionReadInput.model_validate(value)
        return {"criterion": self._read(request).model_dump(mode="json")}

    async def profile_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileListInput.model_validate(value)
        profiles = self._store.list_profiles(request.enabled_only)
        if request.domain_hint is not None:
            profiles = tuple(item for item in profiles if item.domain_hint == request.domain_hint)
        return {"profiles": [item.model_dump(mode="json") for item in profiles]}

    async def profile_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileReadInput.model_validate(value)
        profile = self._store.read_profile(request.profile_ref, request.version)
        if profile is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "profile not found")
        return {"profile": profile.model_dump(mode="json")}

    async def reference_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReferenceListInput.model_validate(value)
        return {
            "references": [
                item.model_dump(mode="json")
                for item in self._store.list_references(request.project_id, request.criterion_id)
            ]
        }

    async def reference_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReferenceReadInput.model_validate(value)
        item = self._store.read_reference(request.reference_candidate_id)
        if item is None or item.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "reference not found")
        return {"reference": item.model_dump(mode="json")}

    async def conflict_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConflictListInput.model_validate(value)
        return {
            "conflicts": [
                item.model_dump(mode="json")
                for item in self._store.list_conflicts(request.project_id, request.criterion_id)
            ]
        }

    async def conflict_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConflictReadInput.model_validate(value)
        item = self._store.read_conflict(request.conflict_id)
        if item is None or item.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "conflict not found")
        return {"conflict": item.model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        self._read(request)
        return {
            "records": [
                item.model_dump(mode="json")
                for item in self._store.list_audit(request.project_id, request.criterion_id)
            ]
        }

    async def compile(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CompileInput.model_validate(value)
        try:
            contract, commit = self._service.compile(
                project_id=request.project_id,
                thread_id=request.thread_id,
                source_span_ids=request.source_span_ids,
                goal_requirement_refs=request.goal_requirement_refs,
                profile_refs=request.profile_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "criterion": contract.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def field_correct(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FieldCorrectInput.model_validate(value)
        if request.field_path.split(".")[0] == "reference_inquiry":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "REFERENCE_PRODUCER_REQUIRED")
        current = self._read_expected(request)
        with self._mutation(current):
            draft = current.model_dump(mode="python")
            old_value = set_path(draft, request.field_path, request.proposed_value)
            field_map = dict(current.field_evidence_map)
            field_map[request.field_path] = request.evidence_span_ids
            authority = dict(current.field_authority_and_version)
            evidence_authority = self._service.evidence_authority(
                request.project_id, request.evidence_span_ids
            )
            authority[request.field_path] = {
                "authority": evidence_authority.value,
                "version": "CORRECTION_CANDIDATE",
                "applicability": (
                    "SOURCE_BOUND"
                    if evidence_authority.value in {"OFFICIAL", "APPROVED"}
                    else "REVIEW_REQUIRED"
                ),
            }
            consistency = current.consistency
            conflict = None
            if old_value is not None and old_value != request.proposed_value:
                conflict = self._service.conflict(
                    current,
                    field_path=request.field_path,
                    candidate_values=(old_value, request.proposed_value),
                    evidence_refs=request.evidence_span_ids,
                )
                consistency = "CONFLICTING"
            revised, commit = self._service.revise(
                current,
                updates={
                    request.field_path.split(".")[0]: draft[request.field_path.split(".")[0]],
                    "field_evidence_map": field_map,
                    "field_authority_and_version": authority,
                    "consistency": consistency,
                    "usage_authorization": "NOT_AUTHORIZED",
                },
                event_type="criteria/updated",
            )
            return {
                "criterion": revised.model_dump(mode="json"),
                "conflict": None if conflict is None else conflict.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def profile_apply(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileApplyInput.model_validate(value)
        current = self._read_expected(request)
        with self._mutation(current):
            profiles = tuple(
                self._store.read_profile(profile_ref, None) for profile_ref in request.profile_refs
            )
            if any(profile is None or not profile.enabled for profile in profiles):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "profile unavailable")
            required = tuple(
                dict.fromkeys(
                    field
                    for profile in profiles
                    if profile is not None
                    for field in profile.required_fields
                )
            )
            present = {
                "construct": bool(current.construct_outcome_definition),
                "verification_spec": bool(current.verification_spec),
                "context_spec": bool(current.context_spec),
                "acceptance_rule": current.acceptance_rule is not None,
                "uncertainty": current.result_and_uncertainty is not None,
                "prespecification": current.prespecification != "UNKNOWN",
                "evaluator_binding": current.evaluator_or_procedure_binding is not None,
            }
            missing = tuple(field for field in required if not present.get(field, False))
            revised, commit = self._service.revise(
                current,
                updates={
                    "profile_refs": request.profile_refs,
                    "missing_fields": missing,
                    "completeness": "COMPLETE" if not missing else "INCOMPLETE",
                    "usage_authorization": "NOT_AUTHORIZED",
                },
                event_type="criteria/profileApplied",
            )
            return {
                "criterion": revised.model_dump(mode="json"),
                "newly_required_fields": list(missing),
                "commit": commit.model_dump(mode="json"),
            }

    async def revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevalidateInput.model_validate(value)
        current = self._read(request)
        with self._mutation(current):
            if (
                request.revision_digest is not None
                and current.revision_digest != request.revision_digest
            ):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision mismatch")
            try:
                revised, commit = self._service.revalidate(current)
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            return {
                "criterion": revised.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def recalculate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecalculateInput.model_validate(value)
        current = self._read(request)
        with self._mutation(current):
            try:
                revised, commit = self._service.recalculate(
                    current,
                    variables=request.variables,
                    evaluator_binding_digest=request.evaluator_binding_digest,
                )
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            return {
                "criterion": revised.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def reference_generate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReferenceGenerateInput.model_validate(value)
        current = self._read_expected(request)
        with self._mutation(current):
            candidate = self._service.reference_candidate(
                current,
                source_scope=request.source_scope,
                scenarios=request.scenarios,
            )
            return {"reference": candidate.model_dump(mode="json")}

    async def change_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeProposeInput.model_validate(value)
        current = self._read_expected(request)
        with self._mutation(current):
            payload: dict[str, JsonValue] = {
                "project_id": current.project_id,
                "criterion_id": current.criterion_id,
                "change_type": request.change_type,
                "proposed_patch": request.proposed_patch,
                "evidence_refs": list(request.evidence_refs),
                "rationale": request.rationale,
                "required_authority": "CRITERION_OWNER_R3",
                "target_revision_digest": current.revision_digest,
            }
            digest = domain_digest("CRITERION_CHANGE_PROPOSAL", "1.0.0", canonical_payload(payload))
            self._service.audit(current, "criteria/changeProposed", {**payload, "digest": digest})
            change_proposal: dict[str, JsonValue] = {
                **payload,
                "digest": digest,
                "state": "HUMAN_REQUIRED_R3",
                "applied": False,
            }
            return {"change_proposal": change_proposal}

    @contextmanager
    def _mutation(self, current: CriterionContractRecord) -> Generator[None]:
        try:
            with self._service.transaction(current):
                yield
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    def _read(self, request: CriterionReadInput) -> CriterionContractRecord:
        current = self._store.read_contract(
            request.project_id, request.criterion_id, request.revision_digest
        )
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "criterion not found")
        return current

    def _read_expected(self, request: RevisionBoundInput) -> CriterionContractRecord:
        current = self._store.read_contract(request.project_id, request.criterion_id, None)
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "criterion not found")
        if current.revision_digest != request.expected_revision_digest:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "criterion revision changed concurrently"
            )
        return current


def set_path(payload: dict[str, object], path: str, value: JsonValue) -> object | None:
    parts = path.split(".")
    if not parts or parts[0] not in {
        "identity",
        "construct_outcome_definition",
        "verification_spec",
        "computation_spec",
        "context_spec",
        "acceptance_rule",
        "evaluator_or_procedure_binding",
        "governance",
    }:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "field path is not correctable")
    current: dict[str, object] = payload
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = cast(dict[str, object], child)
    old_value = current.get(parts[-1])
    current[parts[-1]] = value
    return old_value
