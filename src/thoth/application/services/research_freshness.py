"""One dependency eligibility calculation for history and live consumers."""

from thoth.domain.enums import ImpactStatus
from thoth.domain.research_basis import BasisCurrentness, ResearchResultBasis
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort


class ResearchFreshnessService:
    def __init__(
        self,
        ledger: LedgerPort,
        projects: ProjectStorePort | None = None,
        governance: GovernanceStorePort | None = None,
        artifacts: ArtifactLedgerPort | None = None,
        operations: OperationStorePort | None = None,
    ) -> None:
        self.ledger = ledger
        self.projects, self.governance, self.artifacts = projects, governance, artifacts
        self.operations = operations

    def evaluate_entity(self, project_id: str, key: str, digest: str) -> BasisCurrentness:
        head = self.ledger.read_heads(project_id).get(key)
        state = self.ledger.read_dependency_states(project_id).get(key)
        if head is None:
            return BasisCurrentness(state="UNAVAILABLE", reasons=("HEAD_UNAVAILABLE",))
        if state == ImpactStatus.INVALIDATED:
            return BasisCurrentness(state="INVALIDATED", reasons=("DEPENDENCY_INVALIDATED",))
        if head != digest or state in {ImpactStatus.STALE, ImpactStatus.RECALCULATION_REQUIRED}:
            return BasisCurrentness(
                state="REVIEW_REQUIRED", reasons=("DEPENDENCY_REVIEW_REQUIRED",)
            )
        return BasisCurrentness(state="CURRENT", execution_eligible=True)

    def evaluate_result(
        self,
        project_id: str,
        basis: ResearchResultBasis | None,
        *,
        operation_id: str | None = None,
        completion: str | None = None,
    ) -> BasisCurrentness:
        if basis is None:
            return BasisCurrentness(state="UNKNOWN_BASIS", reasons=("LEGACY_BASIS_UNKNOWN",))
        issues = [
            self.evaluate_entity(project_id, key, digest)
            for key, digest in basis.effective_expected_heads.items()
        ]
        if self.operations is not None and operation_id is not None:
            operation = self.operations.read(operation_id)
            if operation is None or operation.project_id != project_id:
                issues.append(
                    BasisCurrentness(state="UNAVAILABLE", reasons=("RESULT_OPERATION_UNAVAILABLE",))
                )
            elif completion == "CHECKPOINT" and operation.state.value in {"FAILED", "CANCELLED"}:
                issues.append(
                    BasisCurrentness(
                        state="REVIEW_REQUIRED", reasons=("ATTEMPT_ENDED_WITH_CHECKPOINT",)
                    )
                )
        request_key = f"{basis.request_ref.entity_type}:{basis.request_ref.entity_id}"
        if (
            basis.request_ref.project_id != project_id
            or self.ledger.read_heads(project_id).get(request_key)
            != basis.request_ref.revision_digest
        ):
            issues.append(
                BasisCurrentness(state="REVIEW_REQUIRED", reasons=("REQUEST_BASIS_CHANGED",))
            )
        if self.projects is not None and self.governance is not None:
            project, policy = (
                self.projects.read(project_id),
                self.governance.read_policy(project_id),
            )
            if (
                project is None
                or policy is None
                or project.cutoff_at != basis.cutoff_at
                or policy.policy_digest != basis.policy_digest
            ):
                issues.append(
                    BasisCurrentness(state="REVIEW_REQUIRED", reasons=("POLICY_OR_CUTOFF_CHANGED",))
                )
            active = {
                binding.artifact_id
                for binding in self.governance.list_source_bindings(project_id)
                if binding.state == "ACTIVE"
            }
            if self.artifacts is not None:
                for ref in basis.source_basis:
                    span = (
                        None if ref.span_id is None else self.artifacts.read_evidence(ref.span_id)
                    )
                    if (
                        span is None
                        or span.artifact_id not in active
                        or span.source_version_id != ref.source_version_id
                        or span.text_sha256 != ref.text_sha256
                    ):
                        issues.append(
                            BasisCurrentness(
                                state="REVIEW_REQUIRED", reasons=("SOURCE_BASIS_CHANGED",)
                            )
                        )
        if basis.coverage != "COMPLETE":
            issues.append(
                BasisCurrentness(
                    state="UNKNOWN_BASIS", reasons=("BASIS_INCOMPLETE", *basis.reasons)
                )
            )
        reasons = tuple(sorted({reason for issue in issues for reason in issue.reasons}))
        for state in ("UNAVAILABLE", "INVALIDATED", "UNKNOWN_BASIS", "REVIEW_REQUIRED"):
            matched = next((issue for issue in issues if issue.state == state), None)
            if matched is not None:
                return BasisCurrentness(state=matched.state, reasons=reasons)
        return BasisCurrentness(state="CURRENT")

    def owner_eligibility(self, project_id: str, owner_digest: str) -> BasisCurrentness:
        revision = self.ledger.read_revision_by_digest(project_id, owner_digest)
        if revision is None:
            return BasisCurrentness(state="UNAVAILABLE", reasons=("OWNER_UNAVAILABLE",))
        return self.evaluate_entity(
            project_id, f"{revision.entity_type.value}:{revision.entity_id}", owner_digest
        )

    def require_action_eligible(self, project_id: str, key: str, digest: str) -> None:
        if self.evaluate_entity(project_id, key, digest).state != "CURRENT":
            raise ValueError("DEPENDENCY_REVIEW_REQUIRED")

    def ineligible_owner_refs(self, project_id: str) -> frozenset[str]:
        states = self.ledger.read_dependency_states(project_id)
        return frozenset(
            digest
            for key, digest in self.ledger.read_heads(project_id).items()
            if states.get(key)
            in {ImpactStatus.STALE, ImpactStatus.INVALIDATED, ImpactStatus.RECALCULATION_REQUIRED}
        )


def boundary_currentness(currentness: BasisCurrentness, boundary_matches: bool) -> BasisCurrentness:
    if boundary_matches:
        return currentness
    return currentness.model_copy(
        update={
            "state": "REVIEW_REQUIRED" if currentness.state == "CURRENT" else currentness.state,
            "reasons": tuple(
                dict.fromkeys((*currentness.reasons, "REQUEST_SOURCE_OR_ACCESS_BASIS_CHANGED"))
            ),
            "execution_eligible": False,
        }
    )
