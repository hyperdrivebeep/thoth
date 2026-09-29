"""Render the CLI shell's research progress and bounded turn summaries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

from rich.console import Console

from thoth.application.services.research_progress_view import progress_view
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.domain.conversation import TuiTurnResult
from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcRequest


async def present_research_progress(
    tui: TuiSessionService, bus: CommandBus, console: Console
) -> None:
    previous = None
    cursor = 0
    while True:
        await asyncio.sleep(1)
        session = tui.current()
        if session.active_project_id is None or session.active_thread_id is None:
            continue
        cursor += 1
        response = await bus.query(
            JsonRpcRequest.model_validate(
                {
                    "id": f"tui-progress:{cursor}",
                    "method": "thread/read",
                    "params": {
                        "_meta": {
                            "idempotencyKey": f"tui-progress:{session.session_id}:"
                            f"{id(tui)}:{cursor}"
                        },
                        "input": {
                            "project_id": session.active_project_id,
                            "thread_id": session.active_thread_id,
                        },
                    },
                }
            )
        )
        if response.result is None:
            continue
        value = response.result.get("value")
        if not isinstance(value, dict):
            continue
        status = progress_view(value)
        if status != previous:
            previous = status
            console.print_json(json.dumps(status))
            console.print(str(status["usage_summary"]), markup=False)
            reason = status.get("terminal_reason")
            rejection = status.get("http_rejection")
            if isinstance(reason, str) and reason:
                detail = "미확인"
                if isinstance(rejection, dict):
                    rejection_kind = cast(dict[str, object], rejection).get("rejection_kind")
                    if rejection_kind:
                        detail = str(rejection_kind)
                console.print(
                    f"연구 상태: 보류 — {reason} / 원인 상세: {detail}",
                    markup=False,
                )


def render_tui_turn(turn: object, console: Console) -> None:
    value = TuiTurnResult.model_validate(turn)
    payload: dict[str, object] = {
        "intent": value.candidate.intent.value,
        "status": value.status.value,
        "project_id": value.session.active_project_id,
        "thread_id": value.session.active_thread_id,
        "message": value.candidate.display_message,
    }
    if value.error_message is not None:
        payload["hold"] = {
            "code": value.error_code,
            "message": value.error_message,
        }
    if value.response:
        payload["result"] = tui_result_summary(value.response)
    console.print_json(json.dumps(payload))


def tui_result_summary(value: Mapping[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in (
        "project_id",
        "thread_id",
        "lifecycle",
        "execution_state",
        "current_object_ids",
        "working_head_digest",
        "status",
        "contract_version",
        "operation_id",
        "request_epoch",
        "request_ref",
        "input_id",
        "input_state",
        "phase",
        "freshness",
        "current_result",
        "previous_result",
        "attempt",
        "budget",
        "settings_digest",
        "selection",
        "effective_settings",
        "model_options",
        "model_settings",
        "answer_outcome",
        "resume_information",
        "completed_stages",
        "usage",
        "failure",
        "execution_summary",
        "model_dispatches",
    ):
        if key in value:
            summary[key] = value[key]
    for key in (
        "criterion_profile_decision",
        "autonomous_acquisition",
        "critical_counter_search",
        "r2_closed_loop",
        "recursive_improvement",
    ):
        child = value.get(key)
        if isinstance(child, dict):
            child_values = cast(dict[str, object], child)
            summary[key] = {
                name: child_values[name]
                for name in ("state", "terminal_state", "hold_reason", "receipt_digest")
                if name in child_values
            }
    portfolio = value.get("portfolio")
    if isinstance(portfolio, dict):
        portfolio_values = cast(dict[str, object], portfolio)
        hypotheses = portfolio_values.get("hypotheses")
        summary["hypothesis_count"] = (
            len(cast(list[object], hypotheses)) if isinstance(hypotheses, list) else 0
        )
    action_plan = value.get("action_plan")
    if isinstance(action_plan, dict):
        action_values = cast(dict[str, object], action_plan)
        alternatives = action_values.get("alternatives")
        summary["action_count"] = (
            len(cast(list[object], alternatives)) if isinstance(alternatives, list) else 0
        )
    for key in ("assessment", "commit", "export", "exports", "activities"):
        if key in value:
            summary[key] = value[key]
    return summary or {"acknowledged": True}
