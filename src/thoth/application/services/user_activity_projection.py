"""Safe deterministic projection of stored research facts for normal thread reads."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable, Iterable, Mapping
from typing import cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from thoth.domain.evidence import EvidenceSpan
from thoth.domain.user_activity import (
    UserActivityEvent,
    UserActivityLocator,
    UserActivityRedaction,
    UserActivityRefs,
    UserActivityResult,
    UserActivityTarget,
    UserActivityTool,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort

SourceActivityContext = Mapping[str, object]

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,159}$")
_CELL = re.compile(r"^\$?[A-Z]{1,4}\$?[1-9][0-9]*(?::\$?[A-Z]{1,4}\$?[1-9][0-9]*)?$")
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret|credential|private[_-]?key|dsn)"
    r"\s*[:=]\s*\S+)"
)
_PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|"
    r"reveal\s+(?:the\s+)?prompt|execute\s+(?:this\s+)?command)"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

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


def _record(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _alias(kind: str, value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _safe_code(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _redaction_classes(value: object, *, source_content: bool = False) -> set[str]:
    classes: set[str] = set()
    if isinstance(value, Mapping):
        record = cast(Mapping[object, object], value)
        for key, item in record.items():
            lowered = str(key).casefold()
            if lowered in {"raw_command", "command", "argv", "stdout", "stderr"}:
                classes.add("raw_command")
                continue
            if lowered in {"prompt", "raw_prompt", "model_payload", "chain_of_thought"}:
                classes.add("prompt_payload")
                continue
            if lowered in {"exact_text", "excerpt", "source_text", "content"}:
                classes.add("unauthorized_source_excerpt")
                classes.update(_redaction_classes(item, source_content=True))
                continue
            if any(
                token in lowered
                for token in ("api_key", "password", "secret", "private_key", "access_token", "dsn")
            ):
                classes.add("secret")
                continue
            classes.update(_redaction_classes(item, source_content=source_content))
    elif isinstance(value, (list, tuple)):
        for item in cast(Iterable[object], value):
            classes.update(_redaction_classes(item, source_content=source_content))
    elif isinstance(value, str):
        if _CONTROL.search(value):
            classes.add("control_characters")
        if _SECRET.search(value) or "-----BEGIN PRIVATE KEY-----" in value:
            classes.add("secret")
        if _LOCAL_PATH.search(value) or value.casefold().startswith("file://"):
            classes.add("local_absolute_path")
        parsed = urlsplit(value) if "://" in value else None
        if parsed is not None and parsed.query:
            classes.add("query_token")
        if parsed is not None and (parsed.username or parsed.password):
            classes.add("secret")
        if source_content and _PROMPT_INJECTION.search(value):
            classes.add("untrusted_source_text")
    return classes


def _redaction(classes: set[str]) -> UserActivityRedaction:
    ordered = tuple(sorted(classes))
    return UserActivityRedaction(
        applied=bool(ordered),
        classes=ordered,
        public_note_ko="민감하거나 신뢰할 수 없는 상세는 숨겼습니다." if ordered else None,
    )


def _public_origin(value: object, classes: set[str]) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value:
        return None, "project-source"
    classes.update(_redaction_classes(value))
    try:
        parsed = urlsplit(value)
    except ValueError:
        classes.add("invalid_uri")
        return None, "project-source"
    try:
        host = (parsed.hostname or "").encode("idna").decode().casefold()
    except (UnicodeError, ValueError):
        classes.add("invalid_uri")
        return None, "project-source"
    if parsed.scheme not in {"http", "https"} or not host:
        if parsed.scheme or _LOCAL_PATH.search(value):
            classes.add("local_absolute_path")
        return None, "project-source"
    internal = host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".lan")
    )
    try:
        internal = internal or not ipaddress.ip_address(host).is_global
    except ValueError:
        internal = internal or "." not in host
    if internal:
        classes.add("internal_host")
        return None, "project-source"
    if parsed.query:
        classes.add("query_token")
    if parsed.fragment:
        classes.add("uri_fragment")
    if parsed.username or parsed.password:
        classes.add("secret")
    return f"{parsed.scheme}://{host}", host[:120]


def _safe_section(value: object, classes: set[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    classes.update(_redaction_classes(value, source_content=True))
    text = _CONTROL.sub(" ", value).strip()
    if (
        len(text) > 120
        or _SECRET.search(text)
        or _LOCAL_PATH.search(text)
        or _PROMPT_INJECTION.search(text)
    ):
        classes.add("untrusted_source_text")
        return None
    return text


def _source_target(
    span_id: object, context: SourceActivityContext, fallback: Mapping[str, object]
) -> tuple[UserActivityTarget, set[str]]:
    classes = _redaction_classes(fallback, source_content=True)
    classes.update(_redaction_classes(context, source_content=True))
    locator = _record(context.get("locator")) or dict(fallback)
    page = locator.get("page") if isinstance(locator.get("page"), int) else None
    line = locator.get("line") if isinstance(locator.get("line"), int) else None
    cell_raw = locator.get("cell_range")
    cell = cell_raw if isinstance(cell_raw, str) and _CELL.fullmatch(cell_raw) else None
    if isinstance(cell_raw, str) and cell is None:
        classes.add("unsafe_locator")
    section = _safe_section(locator.get("section"), classes)
    safe_locator = UserActivityLocator(
        page=page if isinstance(page, int) and page >= 1 else None,
        section=section,
        cell=cell,
        line_start=line if isinstance(line, int) and line >= 1 else None,
        line_end=line if isinstance(line, int) and line >= 1 else None,
    )
    display_parts: list[str] = []
    if safe_locator.page is not None:
        display_parts.append(f"p.{safe_locator.page}")
    if safe_locator.cell is not None:
        display_parts.append(safe_locator.cell)
    if safe_locator.line_start is not None:
        display_parts.append(f"line {safe_locator.line_start}")
    safe_uri, host_alias = _public_origin(context.get("source_uri"), classes)
    cutoff = context.get("cutoff_state")
    authority = context.get("authority_state")
    currentness = (
        "current"
        if cutoff == "ELIGIBLE"
        else "unknown_time"
        if cutoff == "UNKNOWN_TIME"
        else "stale"
        if cutoff == "AFTER_CUTOFF"
        else "access_unverified"
    )
    access_state = (
        "out_of_scope"
        if cutoff == "PROHIBITED_CONTEXT" or authority == "NOT_ADMISSIBLE"
        else "allowed"
        if context
        else "unknown"
    )
    digest = context.get("text_sha256")
    short_hash = (
        str(digest)[:12]
        if isinstance(digest, str) and re.fullmatch(r"[A-Fa-f0-9]{64}", digest)
        else None
    )
    return (
        UserActivityTarget(
            kind="span",
            title="연결된 자료",
            display_ref=" · ".join(display_parts) or None,
            host_alias=host_alias,
            safe_uri=safe_uri,
            locator=safe_locator,
            source_version_id=_alias("source-version", context.get("source_version_id")),
            hash_short=short_hash,
            currentness=currentness,
            access_state=access_state,
        ),
        classes,
    )


def _relation(context: SourceActivityContext, *, stale: bool) -> str:
    if context.get("cutoff_state") == "PROHIBITED_CONTEXT" or context.get(
        "authority_state"
    ) == "NOT_ADMISSIBLE":
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
                severity="warning" if relation in {"inconclusive", "stale"} else "blocked"
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
            result=UserActivityResult(
                counts={"hypothesis": len(hypotheses), "reviewed": reviewed}
            ),
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
            refs=UserActivityRefs(
                operation_alias=_alias("operation", attempt.get("operation_id"))
            ),
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
            state="blocked"
            if blocked_access or policy
            else "timed_out"
            if timeout
            else "failed",
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
