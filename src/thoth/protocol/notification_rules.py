"""Append conditional notifications in the public facade's existing evaluation order."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue


def append_research_notifications(
    method: str, result: dict[str, JsonValue], values: list[str]
) -> None:
    if method == "project/source/disconnect":
        binding = result.get("binding")
        state = binding.get("state") if isinstance(binding, dict) else None
        values.append(
            "project/source/revoked" if state == "REVOKED" else "project/source/disconnected"
        )
        if state == "REVOKED":
            values.append("evidence/invalidated")
            values.append("criteria/invalidated")
            values.append("outcome/invalidated")
    elif method == "thread/input":
        if result.get("queued") is True:
            values.append("thread/inputQueued")
        else:
            values.append("thread/inputAccepted")
            cycle = result
            assessment = cycle.get("assessment")
            raw_statuses: object = (
                cast(dict[object, object], assessment).get("derived_status")
                if isinstance(assessment, dict)
                else None
            )
            statuses = cast(list[object], raw_statuses) if isinstance(raw_statuses, list) else []
            if any(
                status in {"EVIDENCE_ACQUISITION_REQUIRED", "EXPERT_INPUT_REQUIRED"}
                for status in statuses
            ):
                values.append("thread/waitingForInput")
            action_plan = cycle.get("action_plan")
            raw_alternatives: object = (
                cast(dict[object, object], action_plan).get("alternatives")
                if isinstance(action_plan, dict)
                else None
            )
            alternatives = (
                cast(list[object], raw_alternatives) if isinstance(raw_alternatives, list) else []
            )
            if any(
                isinstance(item, dict)
                and cast(dict[object, object], item).get("execution_authority")
                == "HUMAN_REQUIRED_R3"
                for item in alternatives
            ):
                values.append("thread/waitingForProtectedAction")
    elif method == "thread/pause":
        state = result.get("execution_state")
        values.extend(
            (
                "thread/pauseRequested" if state == "PAUSE_PENDING" else "thread/paused",
                "thread/checkpointCreated",
                "thread/statusChanged",
            )
        )
    elif method in {"thread/stop", "investigation/stop"}:
        if method == "thread/stop":
            values.extend(
                (
                    "thread/stopRequested",
                    "thread/stopped",
                    "thread/cycleTerminated",
                    "thread/statusChanged",
                    "thread/checkpointCreated",
                )
            )
    if method in {"investigation/start", "investigation/update", "investigation/resume"}:
        investigation = result.get("investigation")
        execution_state = (
            cast(dict[object, object], investigation).get("execution_state")
            if isinstance(investigation, dict)
            else None
        )
        if execution_state in {"WAITING_INPUT", "WAITING_ACCESS", "WAITING_BUDGET"}:
            values.append("investigation/waitingForInput")


def append_decision_notifications(
    method: str, result: dict[str, JsonValue], values: list[str]
) -> None:
    if method in {"object/materialize", "object/followup/create"}:
        values.append("object/candidateCreated")
        if isinstance(result.get("object"), dict):
            values.append("object/materialized")
    if method == "object/attention/acknowledge":
        attention = result.get("attention")
        state = (
            cast(dict[object, object], attention).get("state")
            if isinstance(attention, dict)
            else None
        )
        values.append("object/attentionCleared" if state == "CLEARED" else "object/updated")
    if method == "action/authorization/decide":
        authorization = result.get("authorization")
        state = (
            cast(dict[object, object], authorization).get("state")
            if isinstance(authorization, dict)
            else None
        )
        values.append(
            "action/authorizationExpired" if state == "EXPIRED" else "action/authorizationDecided"
        )
    if method == "execution/reconcile":
        attempt = result.get("attempt")
        state = (
            cast(dict[object, object], attempt).get("state") if isinstance(attempt, dict) else None
        )
        state_event = {
            "SUCCEEDED": "execution/attemptSucceeded",
            "PARTIAL": "execution/attemptPartial",
            "FAILED": "execution/attemptFailed",
            "UNKNOWN_COMPLETION": "execution/unknownCompletion",
        }.get(str(state))
        if state_event is not None:
            values.append(state_event)
        execution = result.get("execution")
        plan_state = (
            cast(dict[object, object], execution).get("state")
            if isinstance(execution, dict)
            else None
        )
        if plan_state == "COMPLETED":
            values.append("execution/planCompleted")
        elif plan_state == "PARTIAL":
            values.append("execution/planPartial")
    if method == "outcome/observation/link":
        values.append("outcome/observationLinked")
        series = result.get("series")
        phase_states = (
            cast(dict[object, object], series).get("phase_states")
            if isinstance(series, dict)
            else None
        )
        if (
            isinstance(phase_states, dict)
            and "READY_TO_ASSESS" in cast(dict[object, object], phase_states).values()
        ):
            values.append("outcome/readyToAssess")
        assessment_refs = (
            cast(dict[object, object], series).get("assessment_refs")
            if isinstance(series, dict)
            else None
        )
        if isinstance(assessment_refs, list) and assessment_refs:
            values.append("outcome/reassessmentRequired")


def append_revision_learning_notifications(
    method: str, result: dict[str, JsonValue], values: list[str]
) -> None:
    if method == "revision/changeSet/validate":
        state = result.get("state")
        values.append("revision/validated" if state == "READY_TO_COMMIT" else "revision/held")
    if method == "revision/changeSet/commit":
        committed = result.get("atomic_commit") is True
        values.append("revision/changeSetCommitted" if committed else "revision/changeSetAborted")
        if committed:
            values.extend(
                (
                    "revision/headChanged",
                    "revision/projectionStale",
                    "revision/invalidated",
                )
            )
            if result.get("contains_restore") is True:
                values.append("revision/restored")
            transitions = result.get("domain_transitions")
            if isinstance(transitions, list):
                values.extend(str(item) for item in transitions)
    if method == "revision/merge/propose":
        values.append("revision/mergeProposed")
        if isinstance(result.get("conflict"), dict):
            values.append("revision/conflictOpened")
    if method == "revision/merge/resolve":
        values.extend(("revision/mergeProposed", "revision/conflictResolved"))
    if method == "revision/baseline/decide":
        if isinstance(result.get("baseline"), dict):
            values.extend(("revision/baselineCreated", "revision/baselineSuperseded"))
        else:
            values.append("revision/rejected")
    if method == "memory/validate":
        values.append("memory/validationStarted")
        outcome = result.get("reducer_outcome")
        values.append(
            {
                "VALIDATED": "memory/validated",
                "HELD": "memory/held",
                "QUARANTINED": "memory/quarantined",
                "REJECTED": "memory/rejected",
            }.get(str(outcome), "memory/held")
        )
    if method == "memory/revalidate":
        lifecycle = result.get("lifecycle")
        if lifecycle == "SUPERSEDED":
            values.append("memory/superseded")
    if method == "improvement/evaluation/assess":
        values.append("improvement/evaluated")
        evaluation = result.get("evaluation")
        payload = (
            cast(dict[object, object], evaluation).get("payload")
            if isinstance(evaluation, dict)
            else None
        )
        if (
            isinstance(payload, dict)
            and cast(dict[object, object], payload).get("critical_guardrail_failure") is True
        ):
            values.append("improvement/guardrailFailed")
    if method == "improvement/exposure/prepare":
        exposure = result.get("exposure")
        payload = (
            cast(dict[object, object], exposure).get("payload")
            if isinstance(exposure, dict)
            else None
        )
        if (
            isinstance(payload, dict)
            and cast(dict[object, object], payload).get("requested_exposure_state") == "CANARY"
        ):
            values.append("improvement/canaryPrepared")


def append_lifecycle_notifications(
    method: str, result: dict[str, JsonValue], values: list[str]
) -> None:
    if method == "receipt/verify":
        verification = result.get("verification")
        state = (
            cast(dict[object, object], verification).get("state")
            if isinstance(verification, dict)
            else None
        )
        values.append("receipt/verified" if state == "VERIFIED" else "receipt/verificationFailed")
    if method == "receipt/bundle/verify":
        values.append(
            "receipt/verified"
            if result.get("integrity_state") == "VALID"
            else "receipt/verificationFailed"
        )
    if method == "closure/readiness/assess":
        values.append("closure/readinessChanged")
        if result.get("blocked") is True:
            values.append("closure/blocked")
    if method == "export/verify":
        verification = result.get("verification")
        state = (
            cast(dict[object, object], verification).get("state")
            if isinstance(verification, dict)
            else None
        )
        values.append("export/verified" if state == "VERIFIED" else "export/verificationFailed")
        payload = (
            cast(dict[object, object], verification).get("payload")
            if isinstance(verification, dict)
            else None
        )
        if (
            isinstance(payload, dict)
            and cast(dict[object, object], payload).get("release_eligible") is True
        ):
            values.append("export/releaseEligible")
