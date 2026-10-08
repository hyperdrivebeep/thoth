"""Derive plan display values and its eligible frontier without changing state."""

from __future__ import annotations

from collections.abc import Callable


def derive_plan(
    steps: tuple[dict[str, object], ...],
    edges: tuple[dict[str, str], ...],
    *,
    string_tuple: Callable[[object], tuple[str, ...]],
    mapping: Callable[[object], dict[str, object]],
) -> dict[str, object]:
    predecessor_targets = {edge["to"] for edge in edges}
    frontier = tuple(
        str(step["step_id"])
        for step in steps
        if str(step["step_id"]) not in predecessor_targets
        and step.get("state") == "READY"
        and step.get("risk_tier") in {"R0", "R1", "R2"}
        and step.get("policy_state") in {"AUTO_ALLOWED", "PREAUTHORIZED"}
    )
    processes = tuple(
        dict.fromkeys(
            process
            for step in steps
            for process in string_tuple(step.get("required_processes", ()))
        )
    )
    roles = tuple(
        dict.fromkeys(
            role for step in steps for role in string_tuple(step.get("required_roles", ()))
        )
    )
    return {
        "cumulative_impact": {
            "per_step": {str(step["step_id"]): step.get("impact", {}) for step in steps},
            "max_risk_tier_display_only": max(
                (str(step.get("risk_tier", "R0")) for step in steps),
                default="R0",
            ),
        },
        "required_process_union": processes,
        "required_role_union": roles,
        "auto_executable_frontier": frontier,
        "point_of_no_return_steps": tuple(
            str(step["step_id"])
            for step in steps
            if mapping(step.get("effect_vector", {})).get("irreversible") is True
        ),
    }
