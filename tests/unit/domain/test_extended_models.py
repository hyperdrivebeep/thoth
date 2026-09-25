from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.actor import ActorRef
from thoth.domain.claim import Claim
from thoth.domain.closure import Closure, Export
from thoth.domain.decision import DecisionObject
from thoth.domain.enums import (
    ActorKind,
    AuthorityState,
    ClosureStatus,
    CutoffState,
    DecisionObjectLifecycle,
    ExecutionAttemptState,
    ExportReleaseState,
    OutcomeValidity,
    ResolutionState,
    RiskTier,
    SupportState,
    VerificationState,
)
from thoth.domain.execution import Execution

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ACTOR = ActorRef(actor_id="human:owner", kind=ActorKind.HUMAN, role="owner")


def test_claim_without_evidence_cannot_be_supported() -> None:
    with pytest.raises(ValidationError, match="without evidence"):
        Claim(
            claim_id="claim:1",
            project_id="project:1",
            object_id="object:1",
            text="unsupported claim",
            evidence_refs=(),
            support_state=SupportState.SUPPORTED,
            authority_state=AuthorityState.UNCLASSIFIED,
            verification_state=VerificationState.NOT_CHECKED,
            cutoff_state=CutoffState.UNKNOWN_TIME,
            uncertainty="no source",
        )


def test_decision_object_cannot_close_while_unresolved() -> None:
    with pytest.raises(ValidationError, match="requires resolved or abstained"):
        DecisionObject(
            object_id="object:1",
            project_id="project:1",
            thread_id="thread:1",
            title="Blocked integration",
            problem="comparison mismatch",
            object_profile="generic-rnd",
            lifecycle=DecisionObjectLifecycle.CLOSED,
            resolution_state=ResolutionState.UNRESOLVED,
            created_at=NOW,
        )


def test_execution_success_does_not_make_outcome_valid_without_outputs() -> None:
    with pytest.raises(ValidationError, match="output references"):
        Execution(
            execution_id="execution:1",
            project_id="project:1",
            action_id="action:1",
            attempt=1,
            risk_tier=RiskTier.R1,
            state=ExecutionAttemptState.SUCCEEDED,
            input_digests=("a" * 64,),
            environment_ref="local",
            outcome_validity=OutcomeValidity.VALID,
            started_at=NOW,
            completed_at=NOW,
        )


def test_closure_and_external_export_require_resolved_effects_and_receipt() -> None:
    with pytest.raises(ValidationError, match="no unresolved"):
        Closure(
            closure_id="closure:1",
            project_id="project:1",
            thread_id="thread:1",
            status=ClosureStatus.CLOSED,
            resolution="done",
            unresolved_refs=("claim:1",),
            retention_policy_ref="retention:1",
            actor=ACTOR,
            closed_at=NOW,
            head_set_digest="a" * 64,
        )
    with pytest.raises(ValidationError, match="release receipt"):
        Export(
            export_id="export:1",
            project_id="project:1",
            purpose="external review",
            audience="reviewer",
            head_set_digest="a" * 64,
            artifact_refs=("artifact:1",),
            receipt_refs=("receipt:1",),
            release_state=ExportReleaseState.EXTERNALLY_RELEASED,
            prepared_by=ACTOR,
        )
