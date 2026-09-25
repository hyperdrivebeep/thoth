"""Explicit host-configured frozen cases; expected outputs never enter executor inputs."""

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.evaluation_run import EvaluationBinding, EvaluationRunError


class FrozenEvaluationCatalog:
    def __init__(self, bindings: tuple[EvaluationBinding, ...]) -> None:
        self._bindings: dict[tuple[str, str], EvaluationBinding] = {}
        for binding in bindings:
            key = (binding.project_id, binding.binding_id)
            if key in self._bindings:
                raise EvaluationRunError("EVALUATION_BINDING_DUPLICATED")
            self._bindings[key] = self._copy(binding)

    @staticmethod
    def _copy(binding: EvaluationBinding) -> EvaluationBinding:
        try:
            return EvaluationBinding.model_validate_json(binding.model_dump_json())
        except ValueError:
            # Validation details may contain scorer-only expected values.
            raise EvaluationRunError("EVALUATION_BINDING_INVALID") from None

    def resolve(self, project_id: str, binding_id: str) -> EvaluationBinding:
        value = self._bindings.get((project_id, binding_id))
        if value is None:
            raise EvaluationRunError("EVALUATION_BINDING_NOT_CONFIGURED")
        return self._copy(value)

    def default_binding(
        self, project_id: str, component: BehaviorArtifactKind
    ) -> EvaluationBinding | None:
        matches = [
            item
            for item in self._bindings.values()
            if item.project_id == project_id and item.component == component
        ]
        if len(matches) > 1:
            raise EvaluationRunError("EVALUATION_BINDING_AMBIGUOUS")
        return None if not matches else self.resolve(project_id, matches[0].binding_id)
