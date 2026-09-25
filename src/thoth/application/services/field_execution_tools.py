from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from thoth.application.services.privacy_guard import (
    reject_sensitive_scalar,
    require_privacy_safe_code,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.field_measurement import (
    BlindScorePackage,
    FieldAdjudicationRecord,
    FieldAssignment,
    FieldAssignmentManifest,
    PrivacySafeFieldExport,
    SealedFieldBaseline,
)

_FORBIDDEN_IDENTITY_KEYS = {
    "email",
    "name",
    "reviewer_email",
    "scorer_email",
    "reviewer_external_ref",
    "scorer_external_ref",
    "participant_id",
    "phone",
    "raw_prompt",
    "raw_content",
    "token",
    "secret",
}
_STRIPPED_KEYS = {"arm", "reviewer_pseudonym", "scorer_pseudonym", "adjudicator_pseudonym"}
_EXPORT_TOP_LEVEL_KEYS = {"protocol_digest", "sessions", "events", "metrics", "scores"}
_EXPORT_SECTION_KEYS = {
    "sessions": {
        "session_id",
        "project_id",
        "protocol_digest",
        "case_id",
        "sequence_position",
        "state",
        "started_at",
        "ended_at",
        "timeout",
        "session_digest",
        "active_milliseconds",
    },
    "events": {
        "event_id",
        "project_id",
        "session_id",
        "event_type",
        "metric_delta",
        "method_code",
        "outcome_code",
        "metadata",
        "event_digest",
        "recorded_at",
    },
    "metrics": {
        "session_id",
        "active_milliseconds",
        "rpc_operation_count",
        "app_switch_count",
        "manual_reentry_count",
        "correction_count",
        "timeout",
        "metrics_digest",
    },
    "scores": {
        "score_id",
        "project_id",
        "session_id",
        "gold_issue_total",
        "critical_issue_detected",
        "decision_completeness_bps",
        "source_span_valid_count",
        "source_span_invalid_count",
        "hard_zero_values",
        "score_digest",
        "recorded_at",
    },
}


def validate_sealed_baseline(
    payload: object,
    *,
    actual_case_digests: dict[str, str],
) -> SealedFieldBaseline:
    baseline = SealedFieldBaseline.model_validate(payload)
    draft = baseline.model_dump(mode="python", exclude={"baseline_digest"})
    expected = domain_digest(
        "FIELD_SEALED_BASELINE",
        "1.0.0",
        canonical_payload(draft),
    )
    if baseline.baseline_digest != expected:
        raise ValueError("sealed field baseline digest mismatch")
    if actual_case_digests != baseline.case_digests:
        raise ValueError("sealed field baseline case digest mismatch")
    return baseline


def generate_counterbalanced_assignments(
    *,
    baseline: SealedFieldBaseline,
    reviewer_pseudonyms: tuple[str, ...],
) -> FieldAssignmentManifest:
    if len(reviewer_pseudonyms) != 6 or len(set(reviewer_pseudonyms)) != 6:
        raise ValueError("main assignment requires six unique reviewer pseudonyms")
    if any("@" in item or not item.startswith("reviewer:") for item in reviewer_pseudonyms):
        raise ValueError("assignment accepts pseudonymous reviewer IDs only")
    cases = tuple(sorted(baseline.case_digests))
    assignments: list[FieldAssignment] = []
    for reviewer_index, reviewer in enumerate(reviewer_pseudonyms):
        sequence = baseline.sequence_matrix[reviewer_index]
        for case_index, case_id in enumerate(cases):
            arm = sequence[case_index % 3]
            draft: dict[str, object] = {
                "assignment_id": (
                    "assignment:"
                    + hashlib.sha256(f"{reviewer}:{case_id}:{arm}".encode()).hexdigest()[:24]
                ),
                "reviewer_pseudonym": reviewer,
                "case_id": case_id,
                "arm": arm,
                "sequence_code": sequence,
                "sequence_position": case_index + 1,
            }
            assignments.append(
                FieldAssignment.model_validate(
                    {
                        **draft,
                        "assignment_digest": domain_digest(
                            "FIELD_ASSIGNMENT",
                            "1.0.0",
                            canonical_payload(draft),
                        ),
                    }
                )
            )
    reviewer_case = {(item.reviewer_pseudonym, item.case_id) for item in assignments}
    if len(reviewer_case) != len(assignments):
        raise ValueError("reviewer/case collision detected")
    reviewer_counts = {
        reviewer: dict(
            Counter(item.arm for item in assignments if item.reviewer_pseudonym == reviewer)
        )
        for reviewer in reviewer_pseudonyms
    }
    case_counts = {
        case_id: dict(Counter(item.arm for item in assignments if item.case_id == case_id))
        for case_id in cases
    }
    expected_counts = {"A": 2, "B": 2, "C": 2}
    all_counts = (*reviewer_counts.values(), *case_counts.values())
    if any(value != expected_counts for value in all_counts):
        raise ValueError("counterbalanced assignment cell counts are invalid")
    draft = {
        "baseline_digest": baseline.baseline_digest,
        "assignments": tuple(assignments),
        "reviewer_arm_counts": reviewer_counts,
        "case_arm_counts": case_counts,
        "collision_free": True,
        "external_sessions": "NOT_RUN",
    }
    return FieldAssignmentManifest.model_validate(
        {
            **draft,
            "manifest_digest": domain_digest(
                "FIELD_ASSIGNMENT_MANIFEST",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )


def build_blind_score_package(
    *,
    session_id: str,
    normalized_assessment: dict[str, object],
    source_digest: str,
) -> BlindScorePackage:
    _reject_sensitive(normalized_assessment)
    lowered = canonical_payload(normalized_assessment).decode().casefold()
    if any(token in lowered for token in ('"arm"', "thoth", "vnvspec", "reviewer")):
        raise ValueError("blind package contains arm, branding, or identity information")
    draft: dict[str, object] = {
        "submission_id": "blind:" + hashlib.sha256(session_id.encode()).hexdigest()[:24],
        "session_id": session_id,
        "normalized_assessment": normalized_assessment,
        "source_digest": source_digest,
        "arm_blinded": True,
        "identity_fields_removed": True,
    }
    return BlindScorePackage.model_validate(
        {
            **draft,
            "package_digest": domain_digest(
                "BLIND_SCORE_PACKAGE",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )


def adjudicate_blind_scores(
    *,
    first: Mapping[str, object],
    second: Mapping[str, object],
    adjudicator_pseudonym: str | None = None,
    resolution: dict[str, int] | None = None,
) -> FieldAdjudicationRecord:
    first_digest = _score_digest(first)
    second_digest = _score_digest(second)
    if first_digest == second_digest:
        raise ValueError("independent blind score digests must differ")
    comparable_fields = (
        "critical_issue_detected",
        "decision_completeness_bps",
        "hard_zero_values",
    )
    agreed = all(first.get(key) == second.get(key) for key in comparable_fields)
    if (adjudicator_pseudonym is None) != (resolution is None):
        raise ValueError("adjudicator pseudonym and resolution must be supplied together")
    if adjudicator_pseudonym is not None and (
        not adjudicator_pseudonym.startswith("adjudicator:") or "@" in adjudicator_pseudonym
    ):
        raise ValueError("adjudicator must be pseudonymous")
    scorer_pseudonyms = {
        value
        for value in (first.get("scorer_pseudonym"), second.get("scorer_pseudonym"))
        if isinstance(value, str)
    }
    if adjudicator_pseudonym is not None and adjudicator_pseudonym in scorer_pseudonyms:
        raise ValueError("adjudicator must be independent from both scorers")
    state = (
        "RESOLVED" if resolution is not None else "AGREED" if agreed else "ADJUDICATION_REQUIRED"
    )
    draft: dict[str, object] = {
        "adjudication_id": "adjudication:"
        + hashlib.sha256(f"{first_digest}:{second_digest}".encode()).hexdigest()[:24],
        "score_digests": (first_digest, second_digest),
        "state": state,
        "resolution": resolution,
        "adjudicator_pseudonym": adjudicator_pseudonym,
        "scorer_identity_exposed": False,
    }
    return FieldAdjudicationRecord.model_validate(
        {
            **draft,
            "record_digest": domain_digest(
                "FIELD_ADJUDICATION",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )


def anonymize_field_export(payload: dict[str, object]) -> PrivacySafeFieldExport:
    _reject_sensitive(payload)
    protocol_digest = payload.get("protocol_digest")
    if not isinstance(protocol_digest, str) or len(protocol_digest) != 64:
        raise ValueError("privacy-safe export requires protocol_digest")
    sanitized = cast(dict[str, object], _strip_fields(payload))
    _validate_export_projection(sanitized)
    draft: dict[str, object] = {
        "protocol_digest": protocol_digest,
        "data": sanitized,
        "privacy_safe": True,
        "external_participant_sessions": "NOT_RUN",
        "external_expert_validation": "NOT_RUN",
        "time_saving_result": "NOT_RUN",
        "wtp_result": "NOT_RUN",
        "d6_claimed": False,
    }
    return PrivacySafeFieldExport.model_validate(
        {
            **draft,
            "export_digest": domain_digest(
                "PRIVACY_SAFE_FIELD_EXPORT",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )


def summarize_session_events(
    events: tuple[dict[str, object], ...],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> dict[str, int | bool]:
    if ended_at < started_at:
        raise ValueError("session end precedes start")
    allowed = {
        "SESSION_START",
        "RPC_OPERATION",
        "SOURCE_OPEN",
        "SOURCE_SEARCH",
        "SOURCE_REOPEN",
        "APP_SWITCH",
        "COPY_RETYPE",
        "MANUAL_MAPPING",
        "MANUAL_VERSION_CHECK",
        "CORRECTION",
        "SUBMIT",
        "SESSION_END",
        "TIMEOUT",
    }
    event_types = [str(item.get("event_type", "")) for item in events]
    if any(item not in allowed for item in event_types):
        raise ValueError("session event is outside the preregistered taxonomy")
    return {
        "active_milliseconds": int((ended_at - started_at).total_seconds() * 1000),
        "app_switch_count": event_types.count("APP_SWITCH"),
        "manual_reentry_count": sum(
            event_types.count(item)
            for item in ("COPY_RETYPE", "MANUAL_MAPPING", "MANUAL_VERSION_CHECK")
        ),
        "correction_count": event_types.count("CORRECTION"),
        "timeout": "TIMEOUT" in event_types,
    }


def _score_digest(value: Mapping[str, object]) -> str:
    digest = value.get("score_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("blind score requires a 64-character score_digest")
    hard_zero = value.get("hard_zero_values")
    if not isinstance(hard_zero, dict):
        raise ValueError("hard-zero score vector must be present and zero")
    hard_zero_values = cast(dict[object, object], hard_zero)
    if any(child != 0 for child in hard_zero_values.values()):
        raise ValueError("hard-zero score vector must be present and zero")
    if digest != field_score_digest(value):
        raise ValueError("blind score digest does not match score contents")
    return digest


def field_score_digest(value: Mapping[str, object]) -> str:
    payload = {str(key): child for key, child in value.items() if str(key) != "score_digest"}
    recorded_at = payload.get("recorded_at")
    if isinstance(recorded_at, str):
        payload["recorded_at"] = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    return domain_digest("FIELD_SCORE", "1.0.0", canonical_payload(payload))


def _reject_sensitive(value: object) -> None:
    if isinstance(value, dict):
        for raw_key, child in cast(dict[object, object], value).items():
            key = str(raw_key).casefold()
            if key in _FORBIDDEN_IDENTITY_KEYS or "email" in key or "raw_prompt" in key:
                raise ValueError("identity or privacy-sensitive field is prohibited")
            reject_sensitive_scalar(child)
            _reject_sensitive(child)
    elif isinstance(value, list | tuple):
        for child in cast(list[object] | tuple[object, ...], value):
            _reject_sensitive(child)


def _strip_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _strip_fields(child)
            for key, child in cast(dict[object, object], value).items()
            if str(key).casefold() not in _STRIPPED_KEYS
        }
    if isinstance(value, list):
        return [_strip_fields(child) for child in cast(list[object], value)]
    if isinstance(value, tuple):
        return tuple(_strip_fields(child) for child in cast(tuple[object, ...], value))
    return value


def _validate_export_projection(payload: dict[str, object]) -> None:
    unknown = set(payload) - _EXPORT_TOP_LEVEL_KEYS
    if unknown:
        raise ValueError("privacy-safe export contains fields outside the approved projection")
    for section, allowed_keys in _EXPORT_SECTION_KEYS.items():
        records = payload.get(section, [])
        if not isinstance(records, list | tuple):
            raise ValueError(f"privacy-safe export section must be an array: {section}")
        for record in cast(list[object] | tuple[object, ...], records):
            if not isinstance(record, dict):
                raise ValueError(f"privacy-safe export record must be an object: {section}")
            mapping = cast(dict[object, object], record)
            if {str(key) for key in mapping} - allowed_keys:
                raise ValueError("privacy-safe export record contains an unapproved field")
            if section == "events" and "metadata" in mapping:
                _validate_export_event_metadata(mapping["metadata"])
            _validate_export_values(mapping)


def _validate_export_values(value: object) -> None:
    if isinstance(value, str):
        require_privacy_safe_code(value)
    elif isinstance(value, dict):
        for child in cast(dict[object, object], value).values():
            _validate_export_values(child)
    elif isinstance(value, list | tuple):
        for child in cast(list[object] | tuple[object, ...], value):
            _validate_export_values(child)


def _validate_export_event_metadata(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("privacy-safe event metadata must be an object")
    metadata = cast(dict[object, object], value)
    allowed_keys = {"tool_code", "from_tool_code", "to_tool_code"}
    if {str(key) for key in metadata} - allowed_keys:
        raise ValueError("privacy-safe event metadata contains an unapproved field")
    if any(not isinstance(child, str) for child in metadata.values()):
        raise ValueError("privacy-safe event metadata values must be bounded codes")
