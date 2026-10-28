from pydantic import JsonValue

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    PromptBehaviorPolicy,
    RetrievalBehaviorInput,
    RetrievalBehaviorPolicy,
    RetrievalFusionPolicy,
    RetrievalLanePolicy,
    RetrievalPolicy,
    WorkflowBehaviorInput,
    WorkflowBehaviorPolicy,
    render_prompt_policy,
    workflow_repair_decision,
)
from thoth.domain.glossary import GlossaryDetail
from thoth.domain.retrieval_case import request_of_case
from thoth.domain.retrieval_lane import (
    GLOSSARY_BM25,
    RETRIEVAL_EVALUATOR_ID,
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
        | type[RetrievalFusionPolicy]
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
        policy_type = self._policy_type
        if issubclass(policy_type, RetrievalFusionPolicy):
            # No lane combination is a default. The baseline is always 2.0.0, so nothing asks.
            raise BehaviorPolicyError("BEHAVIOR_POLICY_INVALID")
        return policy_type()

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
    policy: RetrievalPolicy, payload: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Evaluate a retrieval policy with the function the research path itself calls."""
    request = RetrievalBehaviorInput.model_validate(payload)
    selection = select_retrieval_context(request_of_case(request), policy)
    output: dict[str, JsonValue] = {
        "selected_refs": [span.span_id for span in selection.selected],
        "excluded_count": len(request.evidence) - len(selection.selected),
        "selected_characters": sum(len(span.exact_text) for span in selection.selected),
        "mode": selection.mode,
        "evaluator": RETRIEVAL_EVALUATOR_ID,
    }
    if selection.provenance:
        output["lane"] = dict(selection.provenance)
    if isinstance(policy, RetrievalFusionPolicy):
        # Only a fused policy writes these keys, so the output of 2.0.0 and 3.0.0 keeps its bytes.
        # `groups` shows whether the case carried structure groups: with none, a grouped lane ranks
        # single spans.
        output["groups"] = 0 if request.catalog is None else len(request.catalog.groups)
        if selection.contributions:
            lanes: list[JsonValue] = []
            for c in selection.contributions:
                lane: dict[str, JsonValue] = {
                    "lane_id": c.lane_id,
                    "lane_version": c.lane_version,
                    "depth": c.depth,
                    "ranked": c.ranked,
                    "credited_refs": list(c.credited_refs),
                    "also_refs": list(c.also_refs),
                }
                if c.lane_id == GLOSSARY_BM25 and selection.glossary is not None:
                    lane["glossary"] = _glossary_record(selection.glossary)
                lanes.append(lane)
            output["lanes"] = lanes
    return output


def _glossary_record(detail: GlossaryDetail) -> dict[str, JsonValue]:
    """What the glossary lane found, beside its contribution.

    `entries_carried` and `digest_verified` say how far the glossary can be trusted: a case carries
    only the entries that can matter for it with the digest of the whole list, and that digest is
    claimed, not recomputed, so `digest_verified` is false for an evaluation case.
    """
    matches = detail.matches
    return {
        "digest": detail.digest,
        "min_confidence": detail.min_confidence,
        "entries_carried": detail.entries_carried,
        "digest_verified": detail.verified,
        "expansions": list(matches.expansions),
        "dropped": matches.dropped,
        "fired": [
            {"matched": f.matched, "en": f.en, "entry_ids": list(f.entry_ids)}
            for f in matches.fired
        ],
        "abstained": [{"matched": a.matched, "reason": a.reason} for a in matches.abstained],
    }


def _wider_retrieval_budget(policy: RetrievalPolicy) -> int:
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


class RetrievalFusionHandler(TypedBehaviorHandler):
    """Retrieval policy 4.0.0: lanes merged by a fixed method; evaluated by the same function."""

    def __init__(self) -> None:
        super().__init__(BehaviorArtifactKind.RETRIEVAL_POLICY, RetrievalFusionPolicy, "4.0.0")

    def evaluate(
        self, policy: BehaviorPolicy, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if not isinstance(policy, RetrievalFusionPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        return evaluate_retrieval(policy, payload)

    # default_policy() is refused by the base class, and propose() keeps the base answer (no
    # variants): lane depths are chosen by measurement.


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
    registry.register(RetrievalFusionHandler())
    registry.register(WorkflowBehaviorHandler())
    return registry
