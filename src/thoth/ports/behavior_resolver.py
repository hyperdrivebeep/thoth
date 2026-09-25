from typing import Protocol

from pydantic import JsonValue

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import BehaviorPolicy


class BehaviorComponentHandlerPort(Protocol):
    kind: BehaviorArtifactKind
    version: str

    def compile(self, content: dict[str, object]) -> BehaviorPolicy: ...
    def default_policy(self) -> BehaviorPolicy: ...
    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]: ...
    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]: ...


class BehaviorComponentRegistryPort(Protocol):
    def register(self, handler: BehaviorComponentHandlerPort) -> None: ...
    def resolve(self, kind: BehaviorArtifactKind, version: str) -> BehaviorComponentHandlerPort: ...
    def components(self) -> tuple[BehaviorArtifactKind, ...]: ...
