"""Compile an exposure candidate without widening its permitted execution stage."""

from pydantic import JsonValue

from thoth.domain.behavior_policy import BehaviorPolicy, BehaviorPolicyError, RetrievalRoutingPolicy
from thoth.ports.behavior_resolver import BehaviorComponentHandlerPort


def budget_value(values: dict[str, JsonValue], name: str) -> int:
    value = values.get(name)
    if type(value) is not int:
        raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_BUDGET_REQUIRED")
    return value


def compile_exposure_policy(
    handler: BehaviorComponentHandlerPort,
    content: dict[str, object],
    stage: str,
) -> BehaviorPolicy:
    policy = handler.compile(content)
    if isinstance(policy, RetrievalRoutingPolicy) and stage != "SHADOW":
        raise BehaviorPolicyError("RETRIEVAL_ROUTING_SHADOW_ONLY")
    return policy
