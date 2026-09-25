from __future__ import annotations

from typing import cast

from pydantic import JsonValue

STATIC_NOTIFICATIONS: dict[str, tuple[str, ...]] = {
    "project/create": ("project/created",),
    "project/activate": ("project/activated", "project/statusChanged"),
    "project/archive": ("project/archived", "project/statusChanged"),
    "project/delete": ("project/archived", "project/statusChanged"),
    "project/metadata/update": ("project/metadata/updated",),
    "project/source/connect": ("project/source/connected",),
    "project/role/assign": ("project/role/assigned",),
    "project/role/revoke": ("project/role/revoked",),
    "project/overlay/update": ("project/overlay/updated",),
    "project/policy/update": ("project/policy/updated",),
    "project/cutoff/update": ("project/cutoff/updated",),
    "project/reference/import": ("project/reference/imported",),
    "thread/start": ("thread/started", "thread/cycleStarted", "thread/statusChanged"),
    "thread/steer": ("thread/steered",),
    "thread/resume": ("thread/resumed", "thread/statusChanged"),
    "thread/fork": ("thread/forked",),
    "thread/metadata/update": ("thread/metadataUpdated",),
    "investigation/start": (
        "investigation/started",
        "investigation/statusChanged",
    ),
    "investigation/update": (
        "investigation/progress",
        "investigation/sufficiencyUpdated",
        "investigation/statusChanged",
    ),
    "investigation/pause": (
        "investigation/checkpointCreated",
        "investigation/statusChanged",
    ),
    "investigation/resume": ("investigation/statusChanged",),
    "investigation/stop": (
        "investigation/completed",
        "investigation/statusChanged",
    ),
    "evidence/source/add": ("evidence/sourceUpdated",),
    "evidence/source/refresh": ("evidence/sourceUpdated",),
    "evidence/source/metadata/correct": ("evidence/sourceUpdated",),
    "evidence/span/correct": ("evidence/updated",),
    "evidence/link/propose": ("evidence/updated",),
    "evidence/link/correct": ("evidence/updated",),
    "evidence/challenge": ("evidence/conflictUpdated",),
    "evidence/revalidate": ("evidence/revalidated",),
    "criteria/compile": ("criteria/compiled",),
    "criteria/field/correct": ("criteria/updated", "criteria/conflictUpdated"),
    "criteria/profile/apply": ("criteria/profileApplied",),
    "criteria/revalidate": ("criteria/readinessUpdated",),
    "criteria/recalculate": ("criteria/recalculated",),
    "criteria/reference/generate": ("criteria/referenceUpdated",),
    "criteria/change/propose": ("criteria/changeProposed",),
    "object/frame/revise": ("object/updated",),
    "object/profile/apply": ("object/profileChanged", "object/invalidated"),
    "object/facet/update": ("object/updated",),
    "object/relation/add": ("object/relationChanged",),
    "object/relation/remove": ("object/relationChanged",),
    "object/revalidate": (
        "object/blockerChanged",
        "object/attentionRaised",
        "object/resolutionChanged",
        "object/verificationChanged",
    ),
    "object/work/replan": ("object/workModeChanged",),
    "object/split/propose": ("object/splitProposed",),
    "object/merge/propose": ("object/mergeProposed",),
    "hypothesis/generate": ("hypothesis/generated", "hypothesis/created"),
    "hypothesis/create": ("hypothesis/created",),
    "hypothesis/revise": (
        "hypothesis/updated",
        "hypothesis/stageChanged",
        "hypothesis/invalidated",
    ),
    "hypothesis/intent/update": (
        "hypothesis/intentChanged",
        "hypothesis/stageChanged",
        "hypothesis/invalidated",
    ),
    "hypothesis/causal/update": ("hypothesis/updated", "hypothesis/invalidated"),
    "hypothesis/relation/add": ("hypothesis/relationChanged",),
    "hypothesis/relation/remove": ("hypothesis/relationChanged",),
    "hypothesis/assumption/add": (
        "hypothesis/assumptionChanged",
        "hypothesis/invalidated",
    ),
    "hypothesis/prediction/bind": (
        "hypothesis/predictionBound",
        "hypothesis/stageChanged",
    ),
    "hypothesis/counterevidence/request": (
        "hypothesis/counterevidenceRequested",
        "hypothesis/stageChanged",
    ),
    "hypothesis/portfolio/compose": ("hypothesis/portfolioUpdated",),
    "hypothesis/portfolio/revalidate": (
        "hypothesis/portfolioUpdated",
        "hypothesis/qualityUpdated",
    ),
    "hypothesis/test/bind": ("hypothesis/testBound",),
    "hypothesis/appraise": (
        "hypothesis/appraisalUpdated",
        "hypothesis/stageChanged",
        "hypothesis/qualityUpdated",
    ),
    "hypothesis/split/propose": ("hypothesis/splitProposed",),
    "hypothesis/merge/propose": ("hypothesis/mergeProposed",),
    "action/generate": ("action/generated", "action/created"),
    "action/create": ("action/created",),
    "action/revise": (
        "action/updated",
        "action/invalidated",
        "action/authorizationStale",
    ),
    "action/portfolio/compose": ("action/portfolioUpdated",),
    "action/portfolio/evaluate": ("action/evaluated", "action/portfolioUpdated"),
    "action/recommend": ("action/recommended", "action/portfolioUpdated"),
    "action/select": ("action/selected", "action/updated"),
    "action/plan/compose": ("action/planComposed", "action/frontierChanged"),
    "action/plan/revise": (
        "action/planUpdated",
        "action/frontierChanged",
        "action/impactChanged",
        "action/authorizationStale",
    ),
    "action/plan/revalidate": ("action/planUpdated", "action/frontierChanged"),
    "action/step/add": (
        "action/planUpdated",
        "action/frontierChanged",
        "action/impactChanged",
    ),
    "action/step/revise": (
        "action/planUpdated",
        "action/frontierChanged",
        "action/impactChanged",
        "action/authorizationStale",
    ),
    "action/impact/recalculate": ("action/impactChanged",),
    "action/policy/classify": ("action/policyChanged", "action/frontierChanged"),
    "action/authorization/prepare": ("action/authorizationPrepared",),
    "action/compensation/create": ("action/compensationCreated", "action/created"),
    "execution/start": (
        "execution/created",
        "execution/started",
        "execution/frontierChanged",
        "execution/stepDispatched",
        "execution/attemptStarted",
    ),
    "execution/pause": ("execution/paused",),
    "execution/resume": (
        "execution/resumed",
        "execution/frontierChanged",
        "execution/stepDispatched",
    ),
    "execution/cancel": (
        "execution/cancelRequested",
        "execution/reconciliationRequired",
        "execution/effectChanged",
    ),
    "execution/retry": ("execution/stepDispatched", "execution/attemptStarted"),
    "execution/reconcile": (
        "execution/reconciliationCompleted",
        "execution/frontierChanged",
        "execution/effectChanged",
    ),
    "execution/observation/link": ("execution/effectChanged",),
    "execution/compensation/propose": ("execution/compensationRequired",),
    "execution/invalidate": ("execution/invalidated",),
    "outcome/series/create": ("outcome/seriesCreated",),
    "outcome/assess": ("outcome/assessed",),
    "outcome/reassess": ("outcome/reassessed", "outcome/superseded"),
    "outcome/attribution/assess": ("outcome/attributionUpdated",),
    "outcome/changeSet/propose": ("outcome/changeSetProposed",),
    "outcome/followup/generate": ("outcome/followupGenerated",),
    "outcome/impact/propose": ("outcome/impactProposed",),
    "revision/propose": ("revision/proposed",),
    "revision/changeSet/create": ("revision/changeSetCreated",),
    "revision/branch/create": ("revision/branchCreated",),
    "revision/restore/propose": ("revision/restoreProposed",),
    "revision/recompute/request": ("revision/recomputeRequested",),
    "revision/baseline/prepare": ("revision/baselinePrepared",),
    "memory/candidate/create": ("memory/candidateCreated",),
    "memory/candidate/classify": ("memory/classified",),
    "memory/changeSet/propose": ("memory/revised",),
    "memory/revalidate": ("memory/revised", "memory/recallEligibilityChanged"),
    "memory/retire/propose": ("memory/retired",),
    "memory/context/build": ("memory/contextBuilt",),
    "memory/projection/rebuild": ("memory/projectionRebuilt",),
    "memory/retention/evaluate": ("memory/expired",),
    "improvement/propose": ("improvement/proposed",),
    "improvement/revise": ("improvement/updated",),
    "improvement/evaluation/plan": ("improvement/evaluationPlanned",),
    "improvement/exposure/prepare": ("improvement/exposurePrepared",),
    "improvement/promotion/prepare": ("improvement/promotionPrepared",),
    "improvement/promotion/decide": ("improvement/promotionDecided",),
    "improvement/rollback/prepare": ("improvement/rollbackPrepared",),
    "improvement/retire/propose": ("improvement/retired",),
    "receipt/seal": ("receipt/sealing", "receipt/sealed"),
    "receipt/bundle/create": ("receipt/bundleCreated",),
    "receipt/correction/create": ("receipt/corrected",),
    "closure/prepare": ("closure/prepared",),
    "closure/decide": ("closure/decided",),
    "closure/followup/create": ("closure/followupCreated",),
    "closure/reopen": ("closure/reopened",),
    "closure/retention/plan": ("closure/retentionChanged",),
    "closure/purge/prepare": ("closure/purgePrepared",),
    "export/plan/create": ("export/planned",),
    "export/snapshot/create": ("export/snapshotSealed",),
    "export/generate": ("export/generating", "export/generated"),
    "export/release/prepare": ("export/releasePrepared",),
    "export/correction/create": ("export/corrected", "export/superseded"),
}

INTERNAL_NOTIFICATION_EVENTS: dict[str, tuple[str, ...]] = {
    "execution.attempt.timed_out": ("execution/attemptTimedOut",),
    "execution.cancel.confirmed": ("execution/cancelConfirmed",),
    "execution.cancel.failed": ("execution/cancelFailed",),
    "improvement.shadow.started": ("improvement/shadowStarted",),
    "improvement.shadow.completed": ("improvement/shadowCompleted",),
    "improvement.canary.started": ("improvement/canaryStarted",),
    "improvement.canary.stopped": ("improvement/canaryStopped",),
    "improvement.promotion.applied": ("improvement/promoted",),
    "improvement.rollback.applied": ("improvement/rolledBack",),
    "export.release.confirmed": ("export/released",),
}

IMPLEMENTED_NOTIFICATIONS = frozenset(
    {
        "project/created",
        "project/activated",
        "project/archived",
        "project/metadata/updated",
        "project/source/connected",
        "project/source/disconnected",
        "project/source/revoked",
        "project/role/assigned",
        "project/role/revoked",
        "project/overlay/updated",
        "project/policy/updated",
        "project/cutoff/updated",
        "project/reference/imported",
        "project/statusChanged",
        "thread/started",
        "thread/inputAccepted",
        "thread/inputQueued",
        "thread/steered",
        "thread/pauseRequested",
        "thread/paused",
        "thread/resumed",
        "thread/stopRequested",
        "thread/stopped",
        "thread/forked",
        "thread/metadataUpdated",
        "thread/checkpointCreated",
        "thread/waitingForInput",
        "thread/waitingForProtectedAction",
        "thread/statusChanged",
        "thread/cycleStarted",
        "thread/cycleTerminated",
        "investigation/started",
        "investigation/progress",
        "investigation/sufficiencyUpdated",
        "investigation/waitingForInput",
        "investigation/checkpointCreated",
        "investigation/statusChanged",
        "investigation/completed",
        "evidence/sourceUpdated",
        "evidence/updated",
        "evidence/conflictUpdated",
        "evidence/invalidated",
        "evidence/revalidated",
        "criteria/compiled",
        "criteria/updated",
        "criteria/profileApplied",
        "criteria/readinessUpdated",
        "criteria/conflictUpdated",
        "criteria/recalculated",
        "criteria/referenceUpdated",
        "criteria/changeProposed",
        "criteria/invalidated",
        "object/candidateCreated",
        "object/materialized",
        "object/updated",
        "object/profileChanged",
        "object/workModeChanged",
        "object/blockerChanged",
        "object/relationChanged",
        "object/attentionRaised",
        "object/attentionCleared",
        "object/resolutionChanged",
        "object/verificationChanged",
        "object/invalidated",
        "object/splitProposed",
        "object/mergeProposed",
        "object/closed",
        "object/completed",
        "object/dispositionChanged",
        "object/superseded",
        "hypothesis/generated",
        "hypothesis/created",
        "hypothesis/updated",
        "hypothesis/intentChanged",
        "hypothesis/stageChanged",
        "hypothesis/relationChanged",
        "hypothesis/assumptionChanged",
        "hypothesis/predictionBound",
        "hypothesis/counterevidenceRequested",
        "hypothesis/portfolioUpdated",
        "hypothesis/qualityUpdated",
        "hypothesis/testBound",
        "hypothesis/appraisalUpdated",
        "hypothesis/invalidated",
        "hypothesis/splitProposed",
        "hypothesis/mergeProposed",
        "hypothesis/retired",
        "action/generated",
        "action/created",
        "action/updated",
        "action/portfolioUpdated",
        "action/evaluated",
        "action/recommended",
        "action/selected",
        "action/planComposed",
        "action/planUpdated",
        "action/frontierChanged",
        "action/impactChanged",
        "action/policyChanged",
        "action/authorizationPrepared",
        "action/authorizationDecided",
        "action/authorizationStale",
        "action/authorizationExpired",
        "action/compensationCreated",
        "action/invalidated",
        "action/authorizationConsumed",
        "action/retired",
        "execution/created",
        "execution/started",
        "execution/frontierChanged",
        "execution/stepDispatched",
        "execution/attemptStarted",
        "execution/attemptSucceeded",
        "execution/attemptPartial",
        "execution/attemptFailed",
        "execution/unknownCompletion",
        "execution/reconciliationRequired",
        "execution/reconciliationCompleted",
        "execution/cancelRequested",
        "execution/effectChanged",
        "execution/paused",
        "execution/resumed",
        "execution/planPartial",
        "execution/planCompleted",
        "execution/compensationRequired",
        "execution/invalidated",
        "execution/attemptTimedOut",
        "execution/cancelConfirmed",
        "execution/cancelFailed",
        "outcome/seriesCreated",
        "outcome/observationLinked",
        "outcome/readyToAssess",
        "outcome/assessed",
        "outcome/reassessmentRequired",
        "outcome/reassessed",
        "outcome/attributionUpdated",
        "outcome/changeSetProposed",
        "outcome/followupGenerated",
        "outcome/impactProposed",
        "outcome/invalidated",
        "outcome/superseded",
        "revision/proposed",
        "revision/validated",
        "revision/held",
        "revision/rejected",
        "revision/changeSetCreated",
        "revision/changeSetCommitted",
        "revision/changeSetAborted",
        "revision/headChanged",
        "revision/branchCreated",
        "revision/mergeProposed",
        "revision/conflictOpened",
        "revision/conflictResolved",
        "revision/restoreProposed",
        "revision/restored",
        "revision/projectionStale",
        "revision/projectionRecomputed",
        "revision/recomputeRequested",
        "revision/baselinePrepared",
        "revision/baselineCreated",
        "revision/baselineSuperseded",
        "revision/invalidated",
        "memory/candidateCreated",
        "memory/classified",
        "memory/validationStarted",
        "memory/validated",
        "memory/revised",
        "memory/held",
        "memory/recallEligibilityChanged",
        "memory/expired",
        "memory/retired",
        "memory/superseded",
        "memory/quarantined",
        "memory/rejected",
        "memory/contextBuilt",
        "memory/projectionRebuilt",
        "memory/committed",
        "memory/conflictDetected",
        "improvement/proposed",
        "improvement/updated",
        "improvement/evaluationPlanned",
        "improvement/evaluated",
        "improvement/exposurePrepared",
        "improvement/canaryPrepared",
        "improvement/guardrailFailed",
        "improvement/promotionPrepared",
        "improvement/promotionDecided",
        "improvement/rollbackPrepared",
        "improvement/retired",
        "improvement/shadowStarted",
        "improvement/shadowCompleted",
        "improvement/canaryStarted",
        "improvement/canaryStopped",
        "improvement/promoted",
        "improvement/rolledBack",
        "receipt/sealing",
        "receipt/sealed",
        "receipt/sealFailed",
        "receipt/verified",
        "receipt/verificationFailed",
        "receipt/bundleCreated",
        "receipt/corrected",
        "closure/readinessChanged",
        "closure/prepared",
        "closure/decided",
        "closure/blocked",
        "closure/followupCreated",
        "closure/reopened",
        "closure/retentionChanged",
        "closure/purgePrepared",
        "export/planned",
        "export/snapshotSealed",
        "export/generating",
        "export/generated",
        "export/verified",
        "export/verificationFailed",
        "export/releaseEligible",
        "export/releasePrepared",
        "export/corrected",
        "export/superseded",
        "export/released",
    }
)


def notifications_for(method: str, result: dict[str, JsonValue]) -> tuple[str, ...]:
    values = list(STATIC_NOTIFICATIONS.get(method, ()))
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
    if method in {"execution/start", "execution/resume", "execution/reconcile"}:
        attempts = result.get("attempts") or result.get("new_attempts")
        if isinstance(attempts, list) and any(
            isinstance(item, dict)
            and cast(dict[object, object], item).get("authorization_digest") is not None
            for item in attempts
        ):
            values.append("action/authorizationConsumed")
    return tuple(dict.fromkeys(values))


def notifications_for_internal_event(event_type: str) -> tuple[str, ...]:
    """Map authenticated worker/deployment callbacks to public notifications."""
    return INTERNAL_NOTIFICATION_EVENTS.get(event_type, ())
