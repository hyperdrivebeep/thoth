from __future__ import annotations

from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import RiskTier
from thoth.domain.policy import AuthoritativeExecutionPolicy
from thoth.domain.project import Project
from thoth.domain.sandbox import (
    SandboxInputSnapshot,
    SandboxNetworkPolicy,
    SandboxResourceLimits,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import IdGeneratorPort


class SandboxTemplateRequired(ValueError):
    """The authoritative project policy has no template for an R2 action family."""


class R2SandboxSpecCompiler:
    def __init__(
        self,
        *,
        artifacts: ArtifactLedgerPort,
        policies: ProjectPolicyReaderPort,
        ledger: LedgerPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._artifacts = artifacts
        self._policies = policies
        self._ledger = ledger
        self._ids = ids

    def compile(
        self,
        *,
        project: Project,
        plan: ActionPlan,
        action: ActionCandidate,
    ) -> SandboxRunSpec:
        if action.risk_tier != RiskTier.R2 or not action.sandbox_required:
            raise ValueError("only sandbox-required R2 actions can compile to SandboxRunSpec")
        if action.external_write:
            raise ValueError("R2 sandbox action cannot write externally")
        stored = self._policies.read_policy(project.project_id)
        if stored is None:
            raise ValueError("authoritative project policy is missing")
        policy = AuthoritativeExecutionPolicy.from_project_policy(stored)
        template = next(
            (
                item
                for item in policy.sandbox_action_templates
                if item.action_family == action.action_family
            ),
            None,
        )
        if template is None:
            raise SandboxTemplateRequired(
                "project policy has no sandbox template for this action family"
            )
        snapshots: dict[str, SandboxInputSnapshot] = {}
        for span_id in action.source_refs:
            span = self._artifacts.read_evidence(span_id)
            if span is None or span.project_id != project.project_id:
                raise ValueError("R2 action input evidence is outside the project")
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if artifact is None or artifact.project_id != project.project_id:
                raise ValueError("R2 action input artifact is outside the project")
            snapshots[artifact.artifact_id] = SandboxInputSnapshot(
                artifact_id=artifact.artifact_id,
                content_sha256=artifact.byte_sha256,
                source_path="UNBOUND",
                target_name=f"{artifact.artifact_id.replace(':', '-')}.input",
            )
        if not snapshots:
            raise ValueError("R2 sandbox compiler requires at least one bound input artifact")
        heads = dict(self._ledger.read_heads(project.project_id))
        current_head_digest = head_set_digest(heads)
        input_values = tuple(snapshots[key] for key in sorted(snapshots))
        context_payload: dict[str, object] = {
            "project_id": project.project_id,
            "project_revision": project.revision,
            "cutoff_at": project.cutoff_at,
            "action_id": action.action_id,
            "plan_id": plan.plan_id,
            "current_head_set_digest": current_head_digest,
            "input_digests": tuple(item.content_sha256 for item in input_values),
            "policy_digest": policy.policy_digest,
            "runtime_profile": template.runtime_profile,
            "image_digest": template.image_digest,
        }
        return SandboxRunSpec(
            project_id=project.project_id,
            project_revision=project.revision,
            cutoff_at=project.cutoff_at,
            attempt_id=self._ids.new("r2-attempt"),
            runtime_profile=SandboxRuntimeProfile(template.runtime_profile),
            image_digest=template.image_digest,
            argv=template.argv,
            input_snapshots=input_values,
            network_policy=SandboxNetworkPolicy(template.network_policy),
            allowed_hosts=template.allowed_hosts,
            resource_limits=SandboxResourceLimits.model_validate(template.resource_limits),
            policy_id=policy.policy_id,
            policy_revision=policy.policy_revision,
            policy_digest=policy.policy_digest,
            action_id=action.action_id,
            action_plan_id=plan.plan_id,
            current_head_set_digest=current_head_digest,
            input_context_digest=domain_digest(
                "R2_SANDBOX_INPUT_CONTEXT",
                "1.0.0",
                canonical_payload(context_payload),
            ),
        )
