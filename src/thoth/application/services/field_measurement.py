from __future__ import annotations

import hashlib
import hmac
from typing import cast

from thoth.application.services.field_execution_tools import field_score_digest
from thoth.application.services.privacy_guard import require_privacy_safe_code
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.field_measurement import (
    FieldEventRecord,
    FieldExportBundle,
    FieldProtocolSeal,
    FieldScoreRecord,
    FieldSessionMetrics,
    FieldSessionRecord,
)
from thoth.ports.field_measurement import (
    FieldMeasurementObserverPort,
    FieldMeasurementStorePort,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_FORBIDDEN_METADATA = frozenset(
    {"name", "email", "text", "content", "prompt", "token", "secret", "credential"}
)
_MANUAL_REENTRY_EVENTS = frozenset({"COPY_RETYPE", "MANUAL_MAPPING", "MANUAL_VERSION_CHECK"})


class FieldMeasurementService(FieldMeasurementObserverPort):
    def __init__(
        self,
        *,
        store: FieldMeasurementStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        pseudonym_secret: bytes,
    ) -> None:
        if len(pseudonym_secret) < 16:
            raise ValueError("field pseudonym secret must be at least 16 bytes")
        self._store = store
        self._clock = clock
        self._ids = ids
        self._secret = pseudonym_secret

    def seal_protocol(
        self,
        *,
        project_id: str,
        protocol_version: str,
        case_digests: dict[str, str],
        arms: tuple[str, ...],
        sequence_matrix: tuple[str, ...],
        baseline_toolchain: tuple[str, ...],
        thresholds: dict[str, int],
        hard_zero_metrics: tuple[str, ...],
    ) -> FieldProtocolSeal:
        if self._store.list_protocols(project_id):
            raise ValueError("field protocol is already sealed before results")
        now = self._clock.now()
        draft: dict[str, object] = {
            "protocol_id": self._ids.new("field-protocol"),
            "project_id": project_id,
            "protocol_version": protocol_version,
            "case_digests": dict(sorted(case_digests.items())),
            "arms": arms,
            "sequence_matrix": sequence_matrix,
            "baseline_toolchain": baseline_toolchain,
            "thresholds": dict(sorted(thresholds.items())),
            "hard_zero_metrics": tuple(sorted(hard_zero_metrics)),
            "state": "SEALED_BEFORE_RESULTS",
            "external_results": "NOT_RUN",
            "sealed_at": now,
        }
        protocol = FieldProtocolSeal.model_validate(
            {
                **draft,
                "protocol_digest": domain_digest(
                    "FIELD_PROTOCOL_SEAL", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        self._store.put_protocol(protocol)
        return protocol

    def start_session(
        self,
        *,
        project_id: str,
        protocol_digest: str,
        reviewer_external_ref: str,
        case_id: str,
        arm: str,
        sequence_position: int,
    ) -> FieldSessionRecord:
        protocol = self._require_protocol(project_id, protocol_digest)
        if case_id not in protocol.case_digests:
            raise ValueError("field case is not preregistered")
        if arm not in protocol.arms:
            raise ValueError("field arm is not preregistered")
        if sequence_position > len(protocol.sequence_matrix):
            raise ValueError("field sequence position is outside the sealed matrix")
        now = self._clock.now()
        draft: dict[str, object] = {
            "session_id": self._ids.new("field-session"),
            "project_id": project_id,
            "protocol_digest": protocol_digest,
            "reviewer_pseudonym": self._pseudonym("reviewer", reviewer_external_ref),
            "case_id": case_id,
            "arm": arm,
            "sequence_position": sequence_position,
            "state": "RUNNING",
            "started_at": now,
            "ended_at": None,
            "timeout": False,
        }
        session = FieldSessionRecord.model_validate(
            {
                **draft,
                "session_digest": domain_digest("FIELD_SESSION", "1.0.0", canonical_payload(draft)),
            }
        )
        self._store.put_session(session)
        self.record_event(
            project_id=project_id,
            session_id=session.session_id,
            event_type="SESSION_START",
            metric_delta=0,
            metadata={},
        )
        return session

    def record_event(
        self,
        *,
        project_id: str,
        session_id: str,
        event_type: str,
        metric_delta: int,
        metadata: dict[str, str | int | bool],
        method_code: str | None = None,
        outcome_code: str | None = None,
    ) -> FieldEventRecord:
        session = self._require_session(project_id, session_id)
        if session.state != "RUNNING":
            raise ValueError("field session is not running")
        self._validate_metadata(event_type, metadata)
        now = self._clock.now()
        draft: dict[str, object] = {
            "event_id": self._ids.new("field-event"),
            "project_id": project_id,
            "session_id": session_id,
            "event_type": event_type,
            "metric_delta": metric_delta,
            "method_code": method_code,
            "outcome_code": outcome_code,
            "metadata": metadata,
            "recorded_at": now,
        }
        event = FieldEventRecord.model_validate(
            {
                **draft,
                "event_digest": domain_digest("FIELD_EVENT", "1.0.0", canonical_payload(draft)),
            }
        )
        self._store.put_event(event)
        return event

    def record_rpc_event(
        self,
        *,
        project_id: str,
        session_id: str,
        method: str,
        outcome: str,
    ) -> None:
        self.record_event(
            project_id=project_id,
            session_id=session_id,
            event_type="RPC_OPERATION",
            metric_delta=1,
            metadata={},
            method_code=method,
            outcome_code=outcome,
        )

    def validate_session(self, *, project_id: str, session_id: str) -> None:
        session = self._require_session(project_id, session_id)
        if session.state != "RUNNING":
            raise ValueError("field measurement session is not running")

    def end_session(
        self, *, project_id: str, session_id: str, timeout: bool
    ) -> tuple[FieldSessionRecord, FieldSessionMetrics]:
        current = self._require_session(project_id, session_id)
        if current.state != "RUNNING":
            raise ValueError("field session already ended")
        ended_at = self._clock.now()
        ended = current.model_copy(
            update={"state": "ENDED", "ended_at": ended_at, "timeout": timeout}
        )
        self._store.put_session(ended)
        events = self._store.list_events(project_id, session_id)
        metric_draft: dict[str, object] = {
            "session_id": session_id,
            "active_milliseconds": max(
                0, int((ended_at - current.started_at).total_seconds() * 1_000)
            ),
            "rpc_operation_count": sum(
                item.metric_delta for item in events if item.event_type == "RPC_OPERATION"
            ),
            "app_switch_count": sum(
                item.metric_delta for item in events if item.event_type == "APP_SWITCH"
            ),
            "manual_reentry_count": sum(
                item.metric_delta for item in events if item.event_type in _MANUAL_REENTRY_EVENTS
            ),
            "correction_count": sum(
                item.metric_delta for item in events if item.event_type == "CORRECTION"
            ),
            "timeout": timeout,
        }
        metrics = FieldSessionMetrics.model_validate(
            {
                **metric_draft,
                "metrics_digest": domain_digest(
                    "FIELD_SESSION_METRICS",
                    "1.0.0",
                    canonical_payload(metric_draft),
                ),
            }
        )
        self._store.put_metrics(metrics, project_id)
        return ended, metrics

    def record_score(
        self,
        *,
        project_id: str,
        session_id: str,
        scorer_external_ref: str,
        values: dict[str, object],
    ) -> FieldScoreRecord:
        session = self._require_session(project_id, session_id)
        if session.state != "ENDED":
            raise ValueError("blind score requires an ended session")
        if session.reviewer_pseudonym == self._pseudonym("reviewer", scorer_external_ref):
            raise ValueError("session reviewer cannot submit an independent blind score")
        protocol = self._require_protocol(project_id, session.protocol_digest)
        hard_zero = cast(dict[str, int], values["hard_zero_values"])
        if set(hard_zero) != set(protocol.hard_zero_metrics):
            raise ValueError("hard-zero vector does not match sealed protocol")
        now = self._clock.now()
        draft: dict[str, object] = {
            "score_id": self._ids.new("field-score"),
            "project_id": project_id,
            "session_id": session_id,
            "scorer_pseudonym": self._pseudonym("scorer", scorer_external_ref),
            "gold_issue_total": values["gold_issue_total"],
            "critical_issue_detected": values["critical_issue_detected"],
            "decision_completeness_bps": values["decision_completeness_bps"],
            "source_span_valid_count": values["source_span_valid_count"],
            "source_span_invalid_count": values["source_span_invalid_count"],
            "hard_zero_values": hard_zero,
            "recorded_at": now,
        }
        score = FieldScoreRecord.model_validate(
            {
                **draft,
                "score_digest": field_score_digest(draft),
            }
        )
        self._store.put_score(score)
        return score

    def build_export(
        self, *, project_id: str, protocol_digest: str, purpose: str
    ) -> FieldExportBundle:
        protocol = self._require_protocol(project_id, protocol_digest)
        sessions = self._store.list_sessions(project_id, protocol_digest)
        events = self._store.list_events(project_id)
        metrics = self._store.list_metrics(project_id)
        scores = self._store.list_scores(project_id)
        now = self._clock.now()
        draft: dict[str, object] = {
            "export_id": self._ids.new("field-export"),
            "project_id": project_id,
            "protocol": protocol,
            "sessions": sessions,
            "events": events,
            "metrics": metrics,
            "scores": scores,
            "purpose": purpose,
            "privacy_safe": True,
            "external_results": "NOT_RUN",
            "field_validated": False,
            "d6_claimed": False,
            "external_participant_sessions": "NOT_RUN",
            "external_expert_validation": "NOT_RUN",
            "time_saving_result": "NOT_RUN",
            "wtp_result": "NOT_RUN",
            "created_at": now,
        }
        bundle = FieldExportBundle.model_validate(
            {
                **draft,
                "bundle_digest": domain_digest(
                    "FIELD_EXPORT_BUNDLE", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        self._store.put_export(bundle)
        return bundle

    def _require_protocol(self, project_id: str, digest: str) -> FieldProtocolSeal:
        value = self._store.read_protocol(project_id, digest)
        if value is None:
            raise ValueError("sealed field protocol not found")
        return value

    def _require_session(self, project_id: str, session_id: str) -> FieldSessionRecord:
        value = self._store.read_session(project_id, session_id)
        if value is None:
            raise ValueError("field session not found in project")
        return value

    def _pseudonym(self, kind: str, value: str) -> str:
        digest = hmac.new(
            self._secret,
            f"{kind}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{kind}:{digest[:24]}"

    @staticmethod
    def _validate_metadata(
        event_type: str,
        metadata: dict[str, str | int | bool],
    ) -> None:
        allowed_keys = {"tool_code"}
        if event_type == "APP_SWITCH":
            allowed_keys.update({"from_tool_code", "to_tool_code"})
        for key, value in metadata.items():
            lowered = key.casefold()
            if any(token in lowered for token in _FORBIDDEN_METADATA):
                raise ValueError("privacy-sensitive field event metadata is prohibited")
            if key not in allowed_keys:
                raise ValueError("field event metadata key is outside the privacy-safe schema")
            if isinstance(value, str) and len(value) > 160:
                raise ValueError("field event metadata code exceeds privacy-safe limit")
            if not isinstance(value, str):
                raise ValueError("field event metadata values must be bounded codes")
            require_privacy_safe_code(value)
