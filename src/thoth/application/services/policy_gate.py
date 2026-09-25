from __future__ import annotations

from typing import Literal

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.governance import ProjectPolicy
from thoth.domain.policy import (
    AuthoritativeExecutionPolicy,
    PolicyDenialBasis,
    PolicyDenialReceipt,
    PolicyExpectation,
)
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.runtime import ClockPort


class PolicyDenied(RuntimeError):
    def __init__(self, message: str, receipt: PolicyDenialReceipt) -> None:
        super().__init__(message)
        self.receipt = receipt


class PolicyGate:
    def __init__(self, *, policies: ProjectPolicyReaderPort, clock: ClockPort) -> None:
        self._policies = policies
        self._clock = clock

    def authorize(
        self,
        *,
        project_id: str,
        action_kind: Literal["CONNECTOR", "SANDBOX"],
        expectation: PolicyExpectation | None,
    ) -> AuthoritativeExecutionPolicy:
        stored = self._policies.read_policy(project_id)
        if stored is None:
            self.deny(
                project_id=project_id,
                action_kind=action_kind,
                basis=PolicyDenialBasis.POLICY_NOT_FOUND,
                message="authoritative project policy is missing",
                authoritative=None,
                expectation=expectation,
            )
        assert stored is not None
        try:
            authoritative = AuthoritativeExecutionPolicy.from_project_policy(stored)
        except (TypeError, ValueError) as exc:
            self.deny(
                project_id=project_id,
                action_kind=action_kind,
                basis=PolicyDenialBasis.POLICY_CONFIGURATION_INVALID,
                message="authoritative project policy is invalid",
                authoritative=None,
                expectation=expectation,
                stored_policy=stored,
            )
            raise AssertionError("unreachable") from exc
        if expectation is None:
            return authoritative
        if expectation.policy_digest != authoritative.policy_digest:
            self.deny(
                project_id=project_id,
                action_kind=action_kind,
                basis=PolicyDenialBasis.POLICY_DIGEST_MISMATCH,
                message="requested policy digest does not match authoritative project policy",
                authoritative=authoritative,
                expectation=expectation,
            )
        if (
            expectation.policy_id != authoritative.policy_id
            or expectation.policy_revision != authoritative.policy_revision
        ):
            self.deny(
                project_id=project_id,
                action_kind=action_kind,
                basis=PolicyDenialBasis.POLICY_BINDING_MISMATCH,
                message="requested policy revision does not match authoritative project policy",
                authoritative=authoritative,
                expectation=expectation,
            )
        return authoritative

    def deny(
        self,
        *,
        project_id: str,
        action_kind: Literal["CONNECTOR", "SANDBOX"],
        basis: PolicyDenialBasis,
        message: str,
        authoritative: AuthoritativeExecutionPolicy | None,
        expectation: PolicyExpectation | None,
        stored_policy: ProjectPolicy | None = None,
    ) -> None:
        policy_id = None if authoritative is None else authoritative.policy_id
        policy_revision = None if authoritative is None else authoritative.policy_revision
        policy_digest = None if authoritative is None else authoritative.policy_digest
        if authoritative is None and stored_policy is not None:
            policy_id = stored_policy.policy_id
            policy_revision = stored_policy.version
            policy_digest = stored_policy.policy_digest
        draft: dict[str, object] = {
            "project_id": project_id,
            "action_kind": action_kind,
            "denial_basis": basis,
            "policy_id": policy_id,
            "policy_revision": policy_revision,
            "policy_digest": policy_digest,
            "requested_policy_id": None if expectation is None else expectation.policy_id,
            "requested_policy_revision": (
                None if expectation is None else expectation.policy_revision
            ),
            "requested_policy_digest": (
                None if expectation is None else expectation.policy_digest
            ),
            "denied_at": self._clock.now(),
            "semantic_truth_certified": False,
        }
        receipt = PolicyDenialReceipt.model_validate(
            {
                **draft,
                "receipt_digest": domain_digest(
                    "POLICY_DENIAL_RECEIPT",
                    "1.0.0",
                    canonical_payload(draft),
                ),
            }
        )
        raise PolicyDenied(message, receipt)
