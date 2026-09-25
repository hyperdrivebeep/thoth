from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.improvement import ImprovementContextBasis, ImprovementEvaluationRequest


def prepared_request() -> ImprovementEvaluationRequest:
    basis = ImprovementContextBasis(
        project_id="project:integrity",
        project_digest="a" * 64,
        head_set_digest="b" * 64,
        policy_id="policy:integrity",
        policy_revision=1,
        policy_digest="c" * 64,
        baseline_set_digest=None,
        registry_digest=None,
        actor_context_digest=None,
        role_assignment_digest=None,
    )
    draft: dict[str, object] = {
        "request_id": "evaluation:integrity",
        "project_id": basis.project_id,
        "thread_id": "thread:integrity",
        "baseline_digest": "d" * 64,
        "candidate_digest": "e" * 64,
        "fixture_digest": "f" * 64,
        "hidden_holdout_digest": "1" * 64,
        "context": basis,
        "max_requests": 8,
        "timeout_seconds": 60,
        "created_at": datetime(2026, 9, 5, tzinfo=UTC),
    }
    return ImprovementEvaluationRequest.model_validate(
        {
            **draft,
            "request_digest": domain_digest(
                "IMPROVEMENT_EVALUATION_REQUEST",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )


@pytest.mark.parametrize(
    "field",
    [
        "baseline_digest",
        "candidate_digest",
        "fixture_digest",
        "hidden_holdout_digest_ref",
        "evaluation_digest",
    ],
)
def test_evaluation_must_bind_all_inputs_even_with_recomputed_output_digest(field: str) -> None:
    request = prepared_request()
    evaluation = DeterministicIndependentImprovementEvaluator().evaluate(
        baseline_digest=request.baseline_digest,
        candidate_digest=request.candidate_digest,
        fixture_digest=request.fixture_digest,
        hidden_holdout_digest=request.hidden_holdout_digest,
        max_requests=8,
        timeout_seconds=60,
    )
    assert ImprovementEvaluationGate.binding_error(request, evaluation) is None
    changed = evaluation.model_copy(update={field: "9" * 64})
    if field != "evaluation_digest":
        changed = changed.model_copy(
            update={
                "evaluation_digest": domain_digest(
                    "IMPROVEMENT_EVALUATION",
                    "1.0.0",
                    canonical_payload(
                        changed.model_dump(mode="python", exclude={"evaluation_digest"})
                    ),
                )
            }
        )
    assert (
        ImprovementEvaluationGate.binding_error(request, changed) == "EVALUATION_BINDING_MISMATCH"
    )


def test_request_payload_change_cannot_keep_old_request_digest() -> None:
    request = prepared_request()
    value = request.model_dump(mode="python")
    value["candidate_digest"] = "2" * 64
    with pytest.raises(ValueError, match="request digest mismatch"):
        ImprovementEvaluationRequest.model_validate(value)


def test_pre_session_request_digest_remains_readable_without_dropping_a_new_binding() -> None:
    request = prepared_request()
    payload = request.model_dump(mode="python", exclude={"request_digest"})
    context = request.context.model_dump(mode="python")
    context.pop("session_digest")
    payload["context"] = context
    legacy_digest = domain_digest(
        "IMPROVEMENT_EVALUATION_REQUEST", "1.0.0", canonical_payload(payload)
    )
    payload["request_digest"] = legacy_digest
    loaded = ImprovementEvaluationRequest.model_validate(payload)
    assert loaded.request_digest == legacy_digest and loaded.context.session_digest is None
    context["session_digest"] = "3" * 64
    with pytest.raises(ValueError, match="request digest mismatch"):
        ImprovementEvaluationRequest.model_validate(payload)
