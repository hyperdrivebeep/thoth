from __future__ import annotations

from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorArtifactState,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.ports.behavior_artifact import (
    BehaviorArtifactCatalogPort,
    BehaviorArtifactStorePort,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class BehaviorCandidateService:
    def __init__(
        self,
        *,
        store: BehaviorArtifactStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        catalog: BehaviorArtifactCatalogPort | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._catalog = catalog

    def ensure_baseline(self, project_id: str, kind: BehaviorArtifactKind) -> BehaviorArtifact:
        values = self._store.list(project_id, kind)
        baseline = next((item for item in values if item.state == "BASELINE"), None)
        if baseline is not None:
            return baseline
        return self._create(
            project_id=project_id,
            kind=kind,
            version="1.0.0",
            state=BehaviorArtifactState.BASELINE,
            content=(
                {"behavior_contract": kind.value, "revision": 1}
                if self._catalog is None
                else self._catalog.baseline_content(kind)
            ),
            parent_digest=None,
        )

    def create_candidate(
        self,
        *,
        project_id: str,
        kind: BehaviorArtifactKind,
        content: dict[str, object],
        parent_digest: str,
        version: str = "1.0.1-candidate",
    ) -> BehaviorArtifact:
        return self._create(
            project_id=project_id,
            kind=kind,
            version=version,
            state=BehaviorArtifactState.CANDIDATE,
            content=content,
            parent_digest=parent_digest,
        )

    def _create(
        self,
        *,
        project_id: str,
        kind: BehaviorArtifactKind,
        version: str,
        state: BehaviorArtifactState,
        content: dict[str, object],
        parent_digest: str | None,
    ) -> BehaviorArtifact:
        digest = domain_digest("BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(content))
        existing = next(
            (item for item in self._store.list(project_id, kind) if item.content_digest == digest),
            None,
        )
        if existing is not None:
            return existing
        artifact = BehaviorArtifact(
            artifact_id=self._ids.new("behavior-artifact"),
            project_id=project_id,
            kind=kind,
            version=version,
            state=state,
            content=content,
            content_digest=digest,
            parent_digest=parent_digest,
            promotion_eligible=kind
            not in {BehaviorArtifactKind.CODE_PATCH, BehaviorArtifactKind.TRAINING_DATA},
            auto_apply_allowed=kind
            in {
                BehaviorArtifactKind.PROMPT_BUNDLE,
                BehaviorArtifactKind.RETRIEVAL_POLICY,
                BehaviorArtifactKind.WORKFLOW_DEFINITION,
                BehaviorArtifactKind.EVALUATOR_CONTRACT,
            },
            created_at=self._clock.now(),
        )
        self._store.add(artifact)
        return artifact
