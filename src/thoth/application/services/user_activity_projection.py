"""Safe deterministic projection of stored research facts for normal thread reads."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from typing import cast

from pydantic import JsonValue

from thoth.application.services.user_activity_redaction import (
    SourceActivityContext,
    _alias,
    _items,
    _record,
    _redaction,
    _redaction_classes,
    _safe_code,
    _source_target,
)
from thoth.application.services.user_activity_terminal import _append_terminal_event
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.user_activity import (
    UserActivityEvent,
    UserActivityRefs,
    UserActivityResult,
    UserActivityTarget,
    UserActivityTool,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort

_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "RESEARCH_PLANNER": (
        "질문 조건 검토 단계를 완료했습니다.",
        "질문의 범위와 확인할 조건을 구조화했습니다.",
    ),
    "EVIDENCE_RERANKER": (
        "근거 후보 정렬을 완료했습니다.",
        "관련성 순서를 정했으며 지지 여부 판정과는 별개입니다.",
    ),
    "SEMANTIC_REVIEWER": (
        "근거 의미 검토 단계를 완료했습니다.",
        "후보 문장이 질문 조건과 어떤 관계인지 검토했습니다.",
    ),
    "REVIEW_ADJUDICATOR": (
        "근거 판정 대조를 완료했습니다.",
        "검토 결과의 충돌과 보류 사유를 대조했습니다.",
    ),
    "SOURCE_PLANNER": (
        "자료 범위 검토를 완료했습니다.",
        "추가로 확인할 수 있는 허용된 자료 범위를 검토했습니다.",
    ),
    "HYPOTHESIS_REVIEWER": (
        "가설 검토 단계를 완료했습니다.",
        "제안된 설명별 판단 상태를 확인했습니다.",
    ),
}


def source_activity_contexts(
    spans: Iterable[EvidenceSpan], artifacts: ArtifactLedgerPort
) -> dict[str, dict[str, object]]:
    """Read authorized source metadata; the projector performs the public sanitization."""
    result: dict[str, dict[str, object]] = {}
    artifact_cache: dict[str, object] = {}
    for span in spans:
        artifact = artifact_cache.get(span.artifact_id)
        if artifact is None:
            artifact = artifacts.read_artifact(span.artifact_id)
            if artifact is not None:
                artifact_cache[span.artifact_id] = artifact
        result[span.span_id] = {
            "artifact_id": span.artifact_id,
            "source_version_id": span.source_version_id,
            "text_sha256": span.text_sha256,
            "support_state": span.support_state.value,
            "authority_state": span.authority_state.value,
            "cutoff_state": span.cutoff_state.value,
            "locator": span.locator.model_dump(mode="python"),
            "source_uri": None if artifact is None else getattr(artifact, "source_uri", None),
        }
    return result


def _relation(context: SourceActivityContext, *, stale: bool) -> str:
    if (
        context.get("cutoff_state") == "PROHIBITED_CONTEXT"
        or context.get("authority_state") == "NOT_ADMISSIBLE"
    ):
        return "access_blocked"
    if stale or context.get("cutoff_state") == "AFTER_CUTOFF":
        return "stale"
    return {
        "SUPPORTED": "supports",
        "CONTRADICTED": "contradicts",
        "REFUTED": "contradicts",
        "UNRESOLVED": "inconclusive",
        "SUPPORTED_CANDIDATE": "selected_candidate",
    }.get(str(context.get("support_state")), "selected_candidate")


def _append_stage_events(value: Mapping[str, object], add: Callable[..., None]) -> None:
    for item in _items(value.get("completed_stages")):
        stage = _record(item)
        role = _safe_code(stage.get("role"))
        if stage.get("state") != "COMPLETED" or role is None:
            continue
        label, why = _STAGE_LABELS.get(
            role,
            ("연구 단계를 완료했습니다.", "저장된 단계 결과를 다음 검토 단계에 연결했습니다."),
        )
        elapsed = stage.get("elapsed_ms")
        add(
            time=stage.get("completed_at") if isinstance(stage.get("completed_at"), str) else None,
            severity="success",
            announce="none",
            phase=role,
            activity_kind="control",
            action="stage_complete",
            label_ko=label,
            why_ko=why,
            state="succeeded",
            research_relation="none",
            result=UserActivityResult(
                duration_ms=elapsed if isinstance(elapsed, int) and elapsed >= 0 else None,
                counts={
                    "model_dispatch": len(_items(stage.get("dispatch_ids"))),
                },
            ),
            redaction=_redaction(set()),
            refs=UserActivityRefs(
                revision_ref=_alias(
                    "revision", _record(stage.get("stage_ref")).get("revision_digest")
                )
            ),
        )


def _append_source_events(
    value: Mapping[str, object],
    contexts: Mapping[str, SourceActivityContext],
    add: Callable[..., None],
) -> tuple[dict[str, object], dict[str, object], str | None]:
    attempt = _record(value.get("attempt"))
    phase = _safe_code(attempt.get("phase"))
    draft = _record(attempt.get("draft_progress"))
    focus = _record(draft.get("evidence_focus"))
    for item in _items(focus.get("locators")):
        locator = _record(item)
        span_id = locator.get("span_id")
        context: SourceActivityContext = (
            contexts.get(str(span_id), {}) if isinstance(span_id, str) else {}
        )
        target, classes = _source_target(span_id, context, locator)
        display = f" {target.display_ref}" if target.display_ref else ""
        add(
            severity="info",
            visibility="default",
            announce="none",
            phase=phase,
            activity_kind="source",
            action="read",
            label_ko=f"연결된 자료{display}을 읽었습니다.",
            why_ko="질문 조건과 관련된 원문 위치를 확인했으며 아직 근거 채택을 뜻하지 않습니다.",
            state="succeeded",
            research_relation="read",
            target=target,
            tool=UserActivityTool(
                display_name="프로젝트 자료",
                family="LOCAL",
                operation="READ",
                sanitized_args={"page": target.locator.page}
                if target.locator is not None and target.locator.page is not None
                else {},
            ),
            redaction=_redaction(classes),
            refs=UserActivityRefs(source_ref=_alias("source", span_id)),
        )
    return attempt, draft, phase


def _append_judgement_events(
    value: Mapping[str, object],
    draft: Mapping[str, object],
    phase: str | None,
    contexts: Mapping[str, SourceActivityContext],
    add: Callable[..., None],
) -> dict[str, object]:
    current = _record(value.get("current_result"))
    previous = _record(value.get("previous_result"))
    manifest = current or previous
    source_refs = [item for item in _items(manifest.get("source_refs")) if isinstance(item, str)]
    if source_refs:
        stale = not bool(current)
        relations: dict[str, int] = {}
        redactions: set[str] = set()
        for span_id in source_refs:
            context = contexts.get(span_id, {})
            relation = _relation(context, stale=stale)
            relations[relation] = relations.get(relation, 0) + 1
            redactions.update(_redaction_classes(context, source_content=True))
        for relation in (
            "supports",
            "contradicts",
            "inconclusive",
            "selected_candidate",
            "stale",
            "access_blocked",
        ):
            count = relations.get(relation, 0)
            if not count:
                continue
            action = {
                "supports": "adopt_support",
                "contradicts": "adopt_counter",
                "inconclusive": "hold",
                "selected_candidate": "select_candidate",
                "stale": "hold",
                "access_blocked": "hold",
            }[relation]
            label = {
                "supports": f"지지 관계가 기록된 근거 {count}곳을 확인했습니다.",
                "contradicts": f"반례 관계가 기록된 근거 {count}곳을 확인했습니다.",
                "inconclusive": f"판정이 끝나지 않은 근거 {count}곳을 보류했습니다.",
                "selected_candidate": f"근거 후보 {count}곳이 결과 범위에 연결되었습니다.",
                "stale": f"현재성이 달라진 자료 {count}곳은 다시 확인해야 합니다.",
                "access_blocked": f"접근 범위를 벗어난 자료 {count}곳은 사용하지 않았습니다.",
            }[relation]
            add(
                severity="warning"
                if relation in {"inconclusive", "stale"}
                else "blocked"
                if relation == "access_blocked"
                else "info",
                announce="assertive" if relation == "access_blocked" else "none",
                phase=phase,
                activity_kind="judgement",
                action=action,
                label_ko=label,
                why_ko="실행 성공과 연구 판단은 별도 상태로 기록됩니다.",
                state="blocked" if relation == "access_blocked" else "succeeded",
                research_relation=relation,
                result=UserActivityResult(counts={"source": count}),
                redaction=_redaction(redactions),
            )

    portfolio = _record(draft.get("portfolio"))
    hypotheses = _items(portfolio.get("hypotheses"))
    review = _record(draft.get("hypothesis_review"))
    decisions = _items(review.get("decisions"))
    if hypotheses:
        reviewed = min(len(decisions), len(hypotheses))
        complete = reviewed == len(hypotheses)
        add(
            severity="success" if complete else "info",
            announce="polite" if complete else "none",
            phase=phase,
            activity_kind="judgement",
            action="screen",
            label_ko=f"가설 {len(hypotheses)}개 중 {reviewed}개를 검토했습니다.",
            why_ko="각 가설의 검토 수를 표시하며 최종 답의 타당성을 자동으로 뜻하지 않습니다.",
            state="succeeded" if complete else "running",
            research_relation="none",
            result=UserActivityResult(counts={"hypothesis": len(hypotheses), "reviewed": reviewed}),
            redaction=_redaction(set()),
        )
    return manifest


def _append_model_events(
    value: Mapping[str, object], phase: str | None, add: Callable[..., None]
) -> None:
    for item in _items(value.get("model_dispatches")):
        dispatch = _record(item)
        state_raw = str(dispatch.get("state") or "")
        observation = _record(dispatch.get("transport_observation"))
        timeout = observation.get("timeout_kind")
        cancelled = observation.get("local_cancel_requested") is True
        if timeout:
            state, severity, label = "timed_out", "warning", "모델 응답 대기 시간이 초과됐습니다."
        elif cancelled and state_raw != "OBSERVED":
            state, severity, label = (
                "cancel_requested",
                "warning",
                "모델 호출 중단을 요청했습니다.",
            )
        elif state_raw == "RESERVED":
            state, severity, label = "running", "info", "모델 검토 응답을 기다리고 있습니다."
        elif state_raw == "OBSERVED":
            state, severity, label = "succeeded", "success", "모델 검토 응답을 받았습니다."
        elif state_raw == "CANCELLED":
            state, severity, label = "cancelled", "warning", "모델 검토가 중단됐습니다."
        else:
            state, severity, label = "failed", "error", "모델 검토 실행에 실패했습니다."
        elapsed = observation.get("elapsed_ms")
        http_status = observation.get("http_status")
        action = "retry" if dispatch.get("retry_of_dispatch_id") else "call_model"
        add(
            severity=severity,
            announce="assertive" if state in {"failed", "timed_out"} else "none",
            phase=phase,
            activity_kind="model",
            action=action,
            label_ko=label,
            why_ko="모델 호출의 실행 상태이며 근거 채택이나 판단 성공을 뜻하지 않습니다.",
            state=state,
            research_relation="none",
            target=UserActivityTarget(
                kind="model", title="모델 검토기", host_alias="configured-model"
            ),
            tool=UserActivityTool(
                display_name="모델 검토기",
                family="MODEL",
                operation="CALL",
            ),
            result=UserActivityResult(
                http_status=http_status
                if isinstance(http_status, int) and 100 <= http_status <= 599
                else None,
                duration_ms=elapsed if isinstance(elapsed, int) and elapsed >= 0 else None,
            ),
            redaction=_redaction(_redaction_classes(dispatch)),
            refs=UserActivityRefs(
                operation_alias=_alias("operation", dispatch.get("operation_id"))
            ),
        )


def project_user_activity_events(
    value: Mapping[str, object],
    *,
    source_context: Mapping[str, SourceActivityContext] | None = None,
) -> list[dict[str, JsonValue]]:
    """Return safe v1 events without raw source text or execution payloads."""
    contexts = source_context or {}
    events: list[dict[str, JsonValue]] = []

    def add(**data: object) -> None:
        seq = len(events) + 1
        seed = "|".join(
            str(data.get(key) or "") for key in ("activity_kind", "action", "phase", "time")
        )
        event = UserActivityEvent.model_validate(
            {
                "event_id": f"activity:{hashlib.sha256(f'{seq}|{seed}'.encode()).hexdigest()[:20]}",
                "seq": seq,
                **data,
            }
        )
        events.append(cast(dict[str, JsonValue], event.model_dump(mode="json", exclude_none=True)))

    _append_stage_events(value, add)
    attempt, draft, phase = _append_source_events(value, contexts, add)
    manifest = _append_judgement_events(value, draft, phase, contexts, add)
    _append_model_events(value, phase, add)
    _append_terminal_event(value, attempt, phase, manifest, add)
    return events
