"""The step_spec check of action/step/add, run before the plan is read or anything is written."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def step_with_id(step_spec: dict[str, JsonValue]) -> tuple[dict[str, object], str]:
    """The step and its id; a missing, empty or non-string step_id is rejected."""

    step = cast(dict[str, object], step_spec)
    step_id = step.get("step_id")
    if not isinstance(step_id, str) or not step_id:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "step_spec.step_id is required")
    return step, step_id
