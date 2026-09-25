from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import (
    ActiveBehaviorSnapshot,
    BehaviorBaselineRevision,
    BehaviorExecutionRecord,
    BehaviorExposureRecord,
    BehaviorSelection,
)
from thoth.domain.model import ModelRequest


class BehaviorExecutionStorePort(Protocol):
    def read(self, project_id: str, exposure_id: str) -> BehaviorExposureRecord | None: ...
    def list(self, project_id: str) -> tuple[BehaviorExposureRecord, ...]: ...
    def append(self, record: BehaviorExposureRecord, *, expected_revision: int) -> None: ...
    def baseline(
        self, project_id: str, component: BehaviorArtifactKind, environment: str, scope_digest: str
    ) -> BehaviorBaselineRevision | None: ...
    def baselines(
        self, project_id: str, component: BehaviorArtifactKind, environment: str
    ) -> tuple[BehaviorBaselineRevision, ...]: ...
    def promote(self, record: BehaviorBaselineRevision, *, expected_revision: int) -> None: ...
    def rollback_baseline(
        self, record: BehaviorBaselineRevision, *, expected_revision: int
    ) -> None: ...


@runtime_checkable
class ModelCostBoundPort(Protocol):
    def quote_max_cost_microunits[T: BaseModel](self, request: ModelRequest[T]) -> int | None: ...


class BehaviorWorkControlPort(Protocol):
    def baseline(
        self, project: str, component: BehaviorArtifactKind, environment: str, thread_id: str | None
    ) -> BehaviorBaselineRevision | None: ...
    def begin(
        self,
        project: str,
        thread: str,
        execution_id: str,
        baselines: tuple[ActiveBehaviorSnapshot, ...],
    ) -> BehaviorSelection: ...
    def before_model(
        self, snapshots: tuple[ActiveBehaviorSnapshot, ...], quote: int | None
    ) -> None: ...
    def validate_work(self, snapshots: tuple[ActiveBehaviorSnapshot, ...]) -> None: ...
    def finish(self, record: BehaviorExecutionRecord) -> None: ...
