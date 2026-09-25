from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class BehaviorArtifactKind(StrEnum):
    PROMPT_BUNDLE = "PROMPT_BUNDLE"
    RETRIEVAL_POLICY = "RETRIEVAL_POLICY"
    WORKFLOW_DEFINITION = "WORKFLOW_DEFINITION"
    EVALUATOR_CONTRACT = "EVALUATOR_CONTRACT"
    CODE_PATCH = "CODE_PATCH"
    TRAINING_DATA = "TRAINING_DATA"


class BehaviorArtifactState(StrEnum):
    BASELINE = "BASELINE"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    RETIRED = "RETIRED"


class BehaviorArtifact(DomainModel):
    artifact_id: str
    project_id: ProjectId
    kind: BehaviorArtifactKind
    version: str
    state: BehaviorArtifactState
    content: dict[str, object]
    content_digest: Sha256
    parent_digest: Sha256 | None = None
    promotion_eligible: bool = False
    auto_apply_allowed: bool = False
    created_at: AwareDatetime


class BehaviorRegistryEntry(DomainModel):
    project_id: ProjectId
    kind: BehaviorArtifactKind
    active_artifact_id: str
    active_digest: Sha256
    baseline_digest: Sha256
    revision: int
    updated_at: AwareDatetime
