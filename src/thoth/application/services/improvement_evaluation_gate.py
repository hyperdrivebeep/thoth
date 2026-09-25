"""Bind a durable evaluation request to current local authority and state."""

from __future__ import annotations

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.behavior_artifact import BehaviorArtifact, BehaviorArtifactKind
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import (
    ImprovementContextBasis,
    ImprovementEvaluation,
    ImprovementEvaluationRequest,
)
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort


class ImprovementEvaluationGate:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        projects: ProjectStorePort,
        governance: GovernanceStorePort,
        behaviors: BehaviorArtifactStorePort,
        baselines: BaselineService,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        sessions: AuthSessionStorePort,
        clock: ClockPort,
    ) -> None:
        self._ledger = ledger
        self._projects = projects
        self._governance = governance
        self._behaviors = behaviors
        self._baselines = baselines
        self._records = records
        self._controls = controls
        self._sessions = sessions
        self._clock = clock

    def capture(self, project_id: str) -> ImprovementContextBasis:
        project = self._projects.read(project_id)
        policy = self._governance.read_policy(project_id)
        if project is None or policy is None or project.policy_binding_ref != policy.policy_id:
            raise ValueError("improvement project or policy basis is unavailable")
        registry = self._behaviors.read_registry(
            project_id, BehaviorArtifactKind.WORKFLOW_DEFINITION
        )
        actor = current_authenticated_actor()
        role_digest = None
        session_digest = None
        if actor is not None:
            session = self._sessions.read(actor.session_id)
            if (
                session is None
                or session.state != "ACTIVE"
                or session.expires_at <= self._clock.now()
                or session.actor_id != actor.actor_id
                or session.project_id != actor.project_id
                or session.role_assignment_id != actor.role_assignment_id
                or session.role != actor.role
                or session.capabilities != actor.capabilities
                or session.data_scopes != actor.data_scopes
            ):
                raise ValueError("improvement authenticated session is no longer current")
            session_digest = domain_digest(
                "IMPROVEMENT_SESSION_BASIS",
                "1.0.0",
                canonical_payload(session.model_dump(mode="python", exclude={"token_digest"})),
            )
            role = next(
                (
                    item
                    for item in self._governance.list_roles(project_id)
                    if item.role_assignment_id == actor.role_assignment_id
                ),
                None,
            )
            if (
                actor.project_id != project_id
                or role is None
                or role.state != "ACTIVE"
                or role.actor_id != actor.actor_id
                or role.role != actor.role
                or (role.scope,) != actor.data_scopes
                or tuple(
                    sorted(
                        tag.removeprefix("CAP_")
                        for tag in role.authority_tags
                        if tag.startswith("CAP_")
                    )
                )
                != actor.capabilities
            ):
                raise ValueError("improvement authenticated authority is no longer current")
            role_digest = domain_digest(
                "IMPROVEMENT_ROLE_BASIS", "1.0.0", canonical_payload(role.model_dump(mode="python"))
            )
        return ImprovementContextBasis(
            project_id=project_id,
            project_digest=domain_digest(
                "IMPROVEMENT_PROJECT_BASIS",
                "1.0.0",
                canonical_payload(project.model_dump(mode="python")),
            ),
            head_set_digest=head_set_digest(self._ledger.read_heads(project_id)),
            policy_id=policy.policy_id,
            policy_revision=policy.version,
            policy_digest=policy.policy_digest,
            baseline_set_digest=self._baselines.composite_current_digest(project_id),
            registry_digest=None
            if registry is None
            else domain_digest(
                "IMPROVEMENT_REGISTRY_BASIS",
                "1.0.0",
                canonical_payload(registry.model_dump(mode="python")),
            ),
            actor_context_digest=None
            if actor is None
            else domain_digest(
                "IMPROVEMENT_ACTOR_BASIS",
                "1.0.0",
                canonical_payload(actor.model_dump(mode="python")),
            ),
            role_assignment_digest=role_digest,
            session_digest=session_digest,
        )

    def active_behavior_baseline(self, project_id: str) -> BehaviorArtifact | None:
        kind = BehaviorArtifactKind.WORKFLOW_DEFINITION
        registry = self._behaviors.read_registry(project_id, kind)
        if registry is None:
            return None
        artifact = self._behaviors.read(project_id, registry.active_artifact_id)
        if (
            artifact is None
            or artifact.project_id != project_id
            or artifact.kind != kind
            or artifact.state not in {"ACTIVE", "BASELINE"}
            or artifact.content_digest != registry.active_digest
            or artifact.content_digest
            != domain_digest(
                "BEHAVIOR_ARTIFACT_CONTENT",
                "1.0.0",
                canonical_payload(artifact.content),
            )
        ):
            raise ValueError("active improvement baseline artifact is unavailable or inconsistent")
        return artifact

    def is_current(self, request: ImprovementEvaluationRequest) -> bool:
        try:
            return self.capture(request.project_id) == request.context
        except ValueError:
            return False

    def current_active_digest(self, project_id: str, fallback: str) -> str:
        registry = self._behaviors.read_registry(
            project_id, BehaviorArtifactKind.WORKFLOW_DEFINITION
        )
        if registry is not None:
            return registry.active_digest
        return self._baselines.composite_current_digest(project_id) or fallback

    def require_pending(self, expected: ControlRecord) -> None:
        current = self._records.read(expected.project_id, expected.namespace, expected.record_id)
        if (
            current is None
            or current.state != "EVALUATING"
            or current.record_digest != expected.record_digest
            or current.version != expected.version
        ):
            raise ValueError("improvement evaluation request changed before finalization")
        stored_request = ImprovementEvaluationRequest.model_validate(current.payload)
        expected_request = ImprovementEvaluationRequest.model_validate(expected.payload)
        if stored_request != expected_request:
            raise ValueError("improvement evaluation request payload changed")

    def transition(self, expected: ControlRecord, state: str) -> None:
        with self._ledger.transaction():
            self.require_pending(expected)
            self._controls.create(
                project_id=expected.project_id,
                namespace=expected.namespace,
                record_type=expected.record_type,
                record_id=expected.record_id,
                state=state,
                payload=expected.payload,
            )

    @staticmethod
    def binding_error(
        request: ImprovementEvaluationRequest,
        evaluation: ImprovementEvaluation,
    ) -> str | None:
        expected = domain_digest(
            "IMPROVEMENT_EVALUATION",
            "1.0.0",
            canonical_payload(evaluation.model_dump(mode="python", exclude={"evaluation_digest"})),
        )
        if (
            evaluation.baseline_digest != request.baseline_digest
            or evaluation.candidate_digest != request.candidate_digest
            or evaluation.fixture_digest != request.fixture_digest
            or evaluation.hidden_holdout_digest_ref != request.hidden_holdout_digest
            or evaluation.evaluation_digest != expected
        ):
            return "EVALUATION_BINDING_MISMATCH"
        return None
