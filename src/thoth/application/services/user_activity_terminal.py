"""Project terminal status into a safe event through the caller-owned add callback."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from thoth.application.services.user_activity_redaction import (
    _alias,
    _items,
    _record,
    _redaction,
    _redaction_classes,
    _safe_code,
)
from thoth.domain.user_activity import UserActivityRefs, UserActivityResult

__all__ = ["_append_terminal_event"]


def _append_terminal_event(
    value: Mapping[str, object],
    attempt: Mapping[str, object],
    phase: str | None,
    manifest: Mapping[str, object],
    add: Callable[..., None],
) -> None:
    operation_state = str(value.get("operation_state") or "UNKNOWN")
    attempt_status = str(attempt.get("status") or "")
    external_effect = str(attempt.get("external_effect_state") or "NONE")
    remote_observation = str(attempt.get("remote_observation") or "UNKNOWN")
    failure = _record(value.get("failure"))
    primary = _record(failure.get("primary"))
    manifest_result = _record(manifest.get("result"))
    raw_reason = primary.get("reason_code") or manifest.get("terminal_reason")
    reason = _safe_code(raw_reason)
    terminal_classes = _redaction_classes(
        {
            "failure": failure,
            "operation_error": value.get("operation_error"),
        }
    )
    if external_effect not in {"", "NONE", "RETURNED"}:
        add(
            severity="warning",
            announce="assertive",
            phase=phase,
            activity_kind="control",
            action="hold",
            label_ko="외부 실행이 끝났는지 확인하지 못했습니다.",
            why_ko="중복 실행하지 않고 readback 또는 명시적 복구가 필요합니다.",
            state="unknown_external_effect",
            research_relation="held",
            result=UserActivityResult(reason_code=reason),
            redaction=_redaction(terminal_classes),
            refs=UserActivityRefs(operation_alias=_alias("operation", attempt.get("operation_id"))),
        )
    elif attempt_status == "CANCEL_REQUESTED" and operation_state != "CANCELLED":
        add(
            severity="warning",
            announce="assertive",
            phase=phase,
            activity_kind="control",
            action="cancel",
            label_ko="현재 연구 작업의 중단을 요청했습니다.",
            why_ko=(
                "원격 종료 확인 상태는 "
                f"{remote_observation if _safe_code(remote_observation) else 'UNKNOWN'}입니다."
            ),
            state="cancel_requested",
            research_relation="held",
            redaction=_redaction(terminal_classes),
        )
    elif operation_state == "CANCELLED":
        add(
            severity="warning",
            announce="assertive",
            phase=phase,
            activity_kind="control",
            action="cancel",
            label_ko="연구 작업이 중단됐습니다.",
            why_ko="중단 전까지 확인된 기록만 부분 상태로 유지합니다.",
            state="cancelled",
            research_relation="held",
            redaction=_redaction(terminal_classes),
        )
    elif operation_state == "FAILED":
        reason_text = reason or "RESEARCH_EXECUTION_FAILED"
        timeout = any(token in reason_text for token in ("TIMEOUT", "TIMED_OUT", "DEADLINE"))
        blocked_access = any(
            token in reason_text for token in ("ACCESS", "AUTH", "SCOPE", "CREDENTIAL")
        )
        policy = "POLICY" in reason_text
        add(
            severity="blocked" if blocked_access or policy else "warning" if timeout else "error",
            announce="assertive",
            phase=phase,
            activity_kind="control",
            action="hold",
            label_ko="자료 접근이 차단됐습니다."
            if blocked_access
            else "정책상 실행하지 않았습니다."
            if policy
            else "시간 제한으로 연구 실행이 멈췄습니다."
            if timeout
            else "연구 실행에 실패했습니다.",
            why_ko="실패한 실행 결과는 새로운 근거나 판단 성공으로 사용하지 않습니다.",
            state="blocked" if blocked_access or policy else "timed_out" if timeout else "failed",
            research_relation="access_blocked" if blocked_access else "held",
            result=UserActivityResult(reason_code=reason),
            redaction=_redaction(terminal_classes),
        )
    elif manifest_result.get("answer_status") == "PARTIAL_HOLD" or manifest.get("phase") == "HOLD":
        add(
            severity="warning",
            announce="assertive",
            phase=phase,
            activity_kind="judgement",
            action="hold",
            label_ko="일부 근거를 확인했지만 답은 보류 상태입니다.",
            why_ko="실행 완료와 별개로 충족되지 않은 연구 조건이 남아 있습니다.",
            state="succeeded",
            research_relation="held",
            result=UserActivityResult(
                counts={"gap": len(_items(manifest.get("gaps")))}, reason_code=reason
            ),
            redaction=_redaction(terminal_classes),
        )
