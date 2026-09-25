"""Resolve immutable executable baseline content without treating legacy pointers as approval."""

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import (
    ActiveBehaviorSnapshot,
    BehaviorExecutionRecord,
    BehaviorSelection,
)
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import sealed_payload
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_execution import BehaviorWorkControlPort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort


class BehaviorSnapshotService:
    def __init__(
        self,
        store: BehaviorArtifactStorePort,
        registry: BehaviorComponentRegistryPort,
        environment: str = "LOCAL",
    ) -> None:
        self._store, self._registry, self._environment = store, registry, environment
        self.control: BehaviorWorkControlPort | None = None

    def capture(
        self, project_id: str, thread_id: str | None = None
    ) -> tuple[ActiveBehaviorSnapshot, ...]:
        return tuple(
            self._baseline(project_id, kind, thread_id) for kind in self._registry.components()
        )

    def prepare_work(self, project: str, thread: str, execution_id: str) -> BehaviorSelection:
        baselines = self.capture(project, thread)
        return (
            BehaviorSelection(snapshots=baselines)
            if self.control is None
            else self.control.begin(project, thread, execution_id, baselines)
        )

    def baseline_snapshot(
        self, project: str, component: BehaviorArtifactKind, thread: str | None
    ) -> ActiveBehaviorSnapshot:
        return self._baseline(project, component, thread)

    def finish_work(self, record: BehaviorExecutionRecord) -> None:
        if self.control is not None:
            self.control.finish(record)

    def _baseline(
        self, project_id: str, kind: BehaviorArtifactKind, thread_id: str | None
    ) -> ActiveBehaviorSnapshot:
        handler = self._registry.resolve(kind, "2.0.0")
        published = (
            None
            if self.control is None
            else self.control.baseline(project_id, kind, self._environment, thread_id)
        )
        if published is not None:
            artifact = self._store.read(project_id, published.artifact_ref)
            if (
                artifact is None
                or artifact.kind != kind
                or artifact.content_digest != published.content_digest
            ):
                raise BehaviorPolicyError("BEHAVIOR_BASELINE_BINDING_MISMATCH")
            policy = handler.compile(artifact.content)
            if (
                domain_digest(
                    "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(artifact.content)
                )
                != published.content_digest
            ):
                raise BehaviorPolicyError("BEHAVIOR_BASELINE_BINDING_MISMATCH")
            return ActiveBehaviorSnapshot.model_validate(
                sealed_payload(
                    "ACTIVE_BEHAVIOR_SNAPSHOT",
                    "snapshot_digest",
                    {
                        "project_id": project_id,
                        "component": kind,
                        "environment": self._environment,
                        "artifact_ref": artifact.artifact_id,
                        "content_digest": artifact.content_digest,
                        "policy": policy,
                        "origin": "BASELINE",
                        "registry_revision": published.revision,
                        "exposure_ref": None,
                        "ignored_registry_digest": None,
                        "reason_code": None,
                    },
                )
            )
        baselines = tuple(
            item
            for item in self._store.list(project_id, kind)
            if item.state == "BASELINE" and item.version == handler.version
        )
        if len(baselines) > 1:
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_AMBIGUOUS")
        baseline = baselines[0] if baselines else None
        policy = handler.default_policy() if baseline is None else handler.compile(baseline.content)
        content = policy.model_dump(mode="python") if baseline is None else baseline.content
        digest = domain_digest("BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(content))
        if baseline is not None and (
            baseline.project_id != project_id
            or baseline.content_digest != digest
            or baseline.kind != kind
            or baseline.content.get("version") != baseline.version
        ):
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_BINDING_MISMATCH")
        pointer = self._store.read_registry(project_id, kind)
        ignored = None if pointer is None else pointer.active_digest
        return ActiveBehaviorSnapshot.model_validate(
            sealed_payload(
                "ACTIVE_BEHAVIOR_SNAPSHOT",
                "snapshot_digest",
                {
                    "project_id": project_id,
                    "component": kind,
                    "environment": self._environment,
                    "artifact_ref": None if baseline is None else baseline.artifact_id,
                    "content_digest": digest,
                    "policy": policy,
                    "origin": "BUILTIN_DEFAULT" if baseline is None else "BASELINE",
                    "registry_revision": 0 if pointer is None else pointer.revision,
                    "exposure_ref": None,
                    "ignored_registry_digest": ignored,
                    "reason_code": None if ignored is None else "LEGACY_POINTER_NOT_EXECUTABLE",
                },
            )
        )
