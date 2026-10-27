from pydantic import JsonValue

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    PromptBehaviorPolicy,
    RetrievalBehaviorInput,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
    WorkflowBehaviorInput,
    WorkflowBehaviorPolicy,
    render_prompt_policy,
    workflow_repair_decision,
)
from thoth.domain.retrieval_lane import (
    RETRIEVAL_EVALUATOR_ID,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)
from thoth.ports.behavior_resolver import BehaviorComponentHandlerPort


class TypedBehaviorHandler:
    version = "2.0.0"

    def __init__(
        self,
        kind: BehaviorArtifactKind,
        policy_type: type[PromptBehaviorPolicy]
        | type[RetrievalBehaviorPolicy]
        | type[RetrievalLanePolicy]
        | type[WorkflowBehaviorPolicy],
        version: str = "2.0.0",
    ) -> None:
        self.kind, self._policy_type, self.version = kind, policy_type, version

    def compile(self, content: dict[str, object]) -> BehaviorPolicy:
        try:
            return self._policy_type.model_validate(content)
        except ValueError:
            raise BehaviorPolicyError("BEHAVIOR_POLICY_INVALID") from None

    def default_policy(self) -> BehaviorPolicy:
        return self._policy_type()

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        raise BehaviorPolicyError("BEHAVIOR_EVALUATION_NOT_CONFIGURED")

    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]:
        del policy, failure_class
        return ()


class PromptBehaviorHandler(TypedBehaviorHandler):
    def __init__(self) -> None:
        super().__init__(BehaviorArtifactKind.PROMPT_BUNDLE, PromptBehaviorPolicy)

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del payload
        if not isinstance(policy, PromptBehaviorPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        return {key: value for key, value in render_prompt_policy(policy).items()}

    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]:
        if not isinstance(policy, PromptBehaviorPolicy) or failure_class != "SEMANTIC":
            return ()
        instruction = "Preserve explicit uncertainty and cite the supplied evidence."
        if instruction in policy.task_guidance:
            return ()
        guidance = (policy.task_guidance + "\n" + instruction).strip()
        return (
            () if len(guidance) > 5000 else (policy.model_copy(update={"task_guidance": guidance}),)
        )


def evaluate_retrieval(
    policy: RetrievalBehaviorPolicy | RetrievalLanePolicy, payload: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Evaluate a retrieval policy with the function the research path itself calls."""
    request = RetrievalBehaviorInput.model_validate(payload)
    catalog = request.catalog
    selection = select_retrieval_context(
        RetrievalRequest(
            request.problem,
            request.queries,
            request.evidence,
            tuple(dict.fromkeys((*request.forced_refs, *request.pinned_refs))),
            None
            if catalog is None
            else CatalogContext(
                catalog.ordered_refs,
                frozenset(catalog.protected_refs),
                catalog.all_observed,
                catalog.structure_count,
            ),
        ),
        policy,
    )
    return {
        "selected_refs": [span.span_id for span in selection.selected],
        "excluded_count": len(request.evidence) - len(selection.selected),
        "selected_characters": sum(len(span.exact_text) for span in selection.selected),
        "mode": selection.mode,
        "evaluator": RETRIEVAL_EVALUATOR_ID,
    }


def _wider_retrieval_budget(policy: RetrievalBehaviorPolicy | RetrievalLanePolicy) -> int:
    return min(160, policy.max_spans * 2) if policy.max_spans < 160 else 80


class RetrievalBehaviorHandler(TypedBehaviorHandler):
    def __init__(self) -> None:
        super().__init__(BehaviorArtifactKind.RETRIEVAL_POLICY, RetrievalBehaviorPolicy)

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if not isinstance(policy, RetrievalBehaviorPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        return evaluate_retrieval(policy, payload)

    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]:
        if not isinstance(policy, RetrievalBehaviorPolicy) or failure_class != "SEMANTIC":
            return ()
        return (policy.model_copy(update={"max_spans": _wider_retrieval_budget(policy)}),)


class RetrievalLaneHandler(TypedBehaviorHandler):
    """Retrieval policy 3.0.0; it is evaluated by the same function as 2.0.0."""

    def __init__(self) -> None:
        super().__init__(BehaviorArtifactKind.RETRIEVAL_POLICY, RetrievalLanePolicy, "3.0.0")

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if not isinstance(policy, RetrievalLanePolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        return evaluate_retrieval(policy, payload)

    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]:
        if not isinstance(policy, RetrievalLanePolicy) or failure_class != "SEMANTIC":
            return ()
        return (policy.model_copy(update={"max_spans": _wider_retrieval_budget(policy)}),)


class WorkflowBehaviorHandler(TypedBehaviorHandler):
    def __init__(self) -> None:
        super().__init__(BehaviorArtifactKind.WORKFLOW_DEFINITION, WorkflowBehaviorPolicy)

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if not isinstance(policy, WorkflowBehaviorPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        request = WorkflowBehaviorInput.model_validate(payload)
        return {"decision": workflow_repair_decision(policy, request.needs_repair)}

    def propose(self, policy: BehaviorPolicy, failure_class: str) -> tuple[BehaviorPolicy, ...]:
        if not isinstance(policy, WorkflowBehaviorPolicy) or failure_class != "SEMANTIC":
            return ()
        return (
            policy.model_copy(update={"max_semantic_repairs": 1 - policy.max_semantic_repairs}),
        )


class BehaviorComponentRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[BehaviorArtifactKind, str], BehaviorComponentHandlerPort] = {}

    def register(self, handler: BehaviorComponentHandlerPort) -> None:
        key = (handler.kind, handler.version)
        if key in self._handlers:
            raise BehaviorPolicyError("BEHAVIOR_COMPONENT_DUPLICATED")
        self._handlers[key] = handler

    def resolve(self, kind: BehaviorArtifactKind, version: str) -> BehaviorComponentHandlerPort:
        handler = self._handlers.get((kind, version))
        if handler is None:
            raise BehaviorPolicyError("BEHAVIOR_COMPONENT_NOT_CONFIGURED")
        return handler

    def components(self) -> tuple[BehaviorArtifactKind, ...]:
        return tuple(sorted({kind for kind, _version in self._handlers}))


def default_behavior_components() -> BehaviorComponentRegistry:
    registry = BehaviorComponentRegistry()
    registry.register(PromptBehaviorHandler())
    registry.register(RetrievalBehaviorHandler())
    registry.register(RetrievalLaneHandler())
    registry.register(WorkflowBehaviorHandler())
    return registry
