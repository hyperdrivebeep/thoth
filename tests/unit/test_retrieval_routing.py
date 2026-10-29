"""Fixed source-count routing, explicit fallback and legacy byte seals."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from thoth.adapters.behavior.registry import default_behavior_components, evaluate_retrieval
from thoth.domain.artifact import SourceLocator
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    FusionMethodSpec,
    RetrievalBehaviorInput,
    RetrievalBehaviorPolicy,
    RetrievalFusionPolicy,
    RetrievalRoutingPolicy,
)
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.retrieval_case import evaluation_case_payload, request_of_case
from thoth.domain.retrieval_embedding import (
    DenseMaterial,
    DenseModelPin,
    DenseScore,
    dense_question_digest,
    dense_source_digest,
)
from thoth.domain.retrieval_fusion import reciprocal_rank_fusion
from thoth.domain.retrieval_lane import (
    CatalogContext,
    RetrievalRequest,
    dense_v1,
    select_retrieval_context,
)
from thoth.ports.embedding import LocalEmbeddingPort


def span(
    i: int, artifact: str = "a", text: str = "orange lantern evidence in this sentence"
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"s{i}",
        project_id="p",
        artifact_id=artifact,
        source_version_id=f"v:{artifact}",
        locator=SourceLocator(line=i + 1),
        exact_text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        extraction_method="synthetic",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.INFORMAL,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        assert model == DenseModelPin()
        self.calls.append((text, role))
        return ((1.0, 0.0) if "orange" in text else (0.0, 1.0)) + (0.0,) * 382


def material(req: RetrievalRequest, encoder: LocalEmbeddingPort) -> DenseMaterial:
    eligible = connected_retrieval_spans(req.evidence)
    query = encoder.encode_one(req.question, role="QUERY", model=DenseModelPin())
    scores: list[DenseScore] = []
    for s in eligible:
        vector = encoder.encode_one(s.exact_text, role="PASSAGE", model=DenseModelPin())
        scores.append(
            DenseScore(
                span_id=s.span_id,
                score=Decimal(str(sum(a * b for a, b in zip(query, vector, strict=True)))),
            )
        )
    return DenseMaterial(
        project_id="p",
        question_sha256=dense_question_digest(req.question),
        source_digest=dense_source_digest(eligible),
        scores=tuple(scores),
    )


def example() -> RetrievalRequest:
    return RetrievalRequest(
        "orange", (), (span(0), span(1, "b"), span(2, "b", "quiet stones by the stream"))
    )


def test_rrf_fixed_equal_weights_dedup_and_id_tie() -> None:
    assert reciprocal_rank_fusion((("b", "b", "a"), ("a", "b"), ("c",))) == ("a", "b", "c")
    assert reciprocal_rank_fusion(()) == ()
    assert reciprocal_rank_fusion((("a", "x", "b"), ("c", "y", "b"))) == (
        "b",
        "a",
        "c",
        "x",
        "y",
    )


def test_no_evidence_and_versions_of_one_artifact_preserve_legacy() -> None:
    for evidence in [(), (span(0), span(1).model_copy(update={"source_version_id": "v:other"}))]:
        req = RetrievalRequest("orange", (), evidence)
        new = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=1))
        old = select_retrieval_context(req, RetrievalBehaviorPolicy(max_spans=1))
        assert new.selected == old.selected
        assert new.routing is not None and new.routing.artifact_count == (1 if evidence else 0)


def test_unknown_time_only_counts_when_no_eligible_evidence_exists() -> None:
    req = example()
    unknown = tuple(
        s.model_copy(update={"cutoff_state": CutoffState.UNKNOWN_TIME}) for s in req.evidence
    )
    result = select_retrieval_context(
        replace(req, evidence=unknown), RetrievalRoutingPolicy(max_spans=1)
    )
    assert result.routing is not None and result.routing.artifact_count == 2
    mixed = select_retrieval_context(
        replace(req, evidence=(req.evidence[0], *unknown[1:])), RetrievalRoutingPolicy(max_spans=1)
    )
    assert mixed.routing is not None and mixed.routing.artifact_count == 1


def test_dense_scores_reject_nonfinite_and_duplicate_ids() -> None:
    for value in ["NaN", "Infinity", "-Infinity", "1.1"]:
        with pytest.raises(ValidationError):
            DenseScore.model_validate({"span_id": "s0", "score": value})
    req = example()
    data = material(req, FakeEncoder()).model_dump(mode="json")
    data["scores"] = [data["scores"][0], data["scores"][0]]
    with pytest.raises(ValidationError, match="DENSE_DUPLICATE_SPAN"):
        DenseMaterial.model_validate(data)


def test_exposure_guard_also_refuses_reconstructed_canary() -> None:
    from thoth.application.services.behavior_exposure_compilation import compile_exposure_policy

    handler = default_behavior_components().resolve(BehaviorArtifactKind.RETRIEVAL_POLICY, "5.0.0")
    data = RetrievalRoutingPolicy().model_dump()
    assert compile_exposure_policy(handler, data, "SHADOW").version == "5.0.0"
    for stage in ["CANARY", "BASELINE"]:
        with pytest.raises(BehaviorPolicyError, match="RETRIEVAL_ROUTING_SHADOW_ONLY"):
            compile_exposure_policy(handler, data, stage)


def test_only_eligible_artifacts_count_and_n1_ignores_dense() -> None:
    req = example()
    excluded = tuple(
        s.model_copy(update={"cutoff_state": CutoffState.AFTER_CUTOFF}) for s in req.evidence[1:]
    )
    req = replace(req, evidence=(req.evidence[0], span(3), *excluded))
    old = select_retrieval_context(req, RetrievalBehaviorPolicy(max_spans=1))
    new = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=1))
    assert new.selected == old.selected
    assert new.provenance == old.provenance and new.mode == old.mode
    assert (
        new.routing is not None and new.routing.artifact_count == 1 and not new.routing.dense_used
    )


def test_original_question_only_dense_and_codec_roundtrip() -> None:
    req = example()
    fake = FakeEncoder()
    dense = material(req, fake)
    req = replace(req, dense=dense)
    assert fake.calls[0] == ("orange", "QUERY")
    changed = replace(req, queries=("quiet stones",))
    assert dense_v1(req, req.evidence) == dense_v1(changed, changed.evidence)
    policy = RetrievalRoutingPolicy(max_spans=2)
    payload = evaluation_case_payload(changed)
    assert request_of_case(RetrievalBehaviorInput.model_validate(payload)).dense == dense
    output = evaluate_retrieval(policy, payload)
    assert output["selected_refs"] == [
        s.span_id for s in select_retrieval_context(changed, policy).selected
    ]
    routing = output["routing"]
    assert isinstance(routing, dict) and routing["dense_used"] is True


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"question_sha256": "0" * 64}, "DENSE_QUESTION_MISMATCH"),
        ({"source_digest": "0" * 64}, "INDEX_STALE"),
        ({"onnx_sha256": "0" * 64}, "MODEL_HASH_MISMATCH"),
        ({"scores": ()}, "INDEX_INCOMPLETE"),
    ],
)
def test_stale_or_missing_material_falls_back_explicitly(
    change: dict[str, object], reason: str
) -> None:
    req = example()
    dense = material(req, FakeEncoder()).model_copy(update=change)
    result = select_retrieval_context(
        replace(req, dense=dense), RetrievalRoutingPolicy(max_spans=1)
    )
    fallback = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=1))
    assert result.selected == fallback.selected
    assert result.routing is not None and result.routing.fallback_reason == reason


def test_wrong_project_is_not_silently_downgraded() -> None:
    req = example()
    dense = material(req, FakeEncoder()).model_copy(update={"project_id": "another"})
    with pytest.raises(ValueError, match="PROJECT_SCOPE"):
        select_retrieval_context(replace(req, dense=dense), RetrievalRoutingPolicy(max_spans=1))


def test_full_catalog_pins_and_budget_frame() -> None:
    req = example()
    catalog = CatalogContext(tuple(s.span_id for s in req.evidence), frozenset(), True, 3)
    full = select_retrieval_context(replace(req, catalog=catalog), RetrievalRoutingPolicy())
    assert (
        full.selected == req.evidence
        and full.routing is not None
        and full.routing.route == "FULL_CATALOG"
    )
    pinned = replace(req, pinned_refs=("s2",), dense=material(req, FakeEncoder()))
    result = select_retrieval_context(
        pinned, RetrievalRoutingPolicy(max_spans=2, character_budget=65)
    )
    assert result.selected[0].span_id == "s2"
    assert len({s.span_id for s in result.selected}) == len(result.selected) <= 2
    assert sum(len(s.exact_text) for s in result.selected) <= 65


def test_fixed_policy_and_no_default() -> None:
    assert (
        TypeAdapter[BehaviorPolicy](BehaviorPolicy)
        .validate_python(RetrievalRoutingPolicy().model_dump())
        .version
        == "5.0.0"
    )
    handler = default_behavior_components().resolve(BehaviorArtifactKind.RETRIEVAL_POLICY, "5.0.0")
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        handler.default_policy()
    for data in [{"exposure": "CANARY"}, {"fusion": {"method_id": "RRF", "k": 61}}]:
        with pytest.raises(ValidationError):
            RetrievalRoutingPolicy.model_validate(data)
    with pytest.raises(ValidationError):
        RetrievalFusionPolicy.model_validate(
            {
                "lanes": [
                    {"lane_id": "LEXICAL_SUBSTRING_V1", "depth": 1},
                    {"lane_id": "GROUPED_BM25", "depth": 1},
                ],
                "fusion": {"method_id": "RRF", "k": 60},
            }
        )
    assert FusionMethodSpec().model_dump() == {
        "method_id": "ROUND_ROBIN_DEDUP",
        "method_version": "1.0.0",
    }


def test_legacy_policy_case_and_output_bytes_have_not_changed() -> None:
    from tests.unit.test_retrieval_case import POLICIES, frozen, requests

    rows = [
        {
            "policy": p.model_dump(mode="json"),
            "input": frozen(r),
            "output": evaluate_retrieval(p, frozen(r)),
        }
        for p in POLICIES.values()
        for r in requests().values()
    ]
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "078f9cb8c06641a684ab24162dcb54ccc094072513c1cb7cad0b53fd14b922af"
    )
