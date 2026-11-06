"""The faster dense digests and score construction give exactly what the original code gave."""

import random
import unicodedata
from decimal import Decimal

import pytest
from tests.unit.test_retrieval_routing import span

from thoth.application.services.dense_material import dense_scores, score_decimal
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_embedding import (
    DenseMaterial,
    DenseScore,
    DenseUnavailableReason,
    dense_material_digest,
    dense_source_digest,
    forget_remembered_frame,
    remembered_frame,
)


def reference_source_digest(evidence: tuple[EvidenceSpan, ...]) -> str:
    """dense_source_digest as it was before the faster implementation."""
    return domain_digest(
        "DENSE_SOURCE",
        "1.0.0",
        canonical_payload(
            {
                "sources": tuple(
                    (s.project_id, s.artifact_id, s.source_version_id, s.span_id, s.text_sha256)
                    for s in evidence
                )
            }
        ),
    )


def reference_material_digest(material: DenseMaterial) -> str:
    """The digest select_routed recorded before the faster implementation."""
    return domain_digest("DENSE_MATERIAL", "1.0.0", canonical_payload(material))


def frame(count: int) -> tuple[EvidenceSpan, ...]:
    return tuple(span(i, f"a{i % 7}", f"text number {i} of the frame") for i in range(count))


def unicode_frame() -> tuple[EvidenceSpan, ...]:
    decomposed = unicodedata.normalize("NFD", "한글 문서")
    assert decomposed != unicodedata.normalize("NFC", decomposed)
    return tuple(
        s.model_copy(
            update={
                "span_id": f"{decomposed}-{s.span_id}",
                "artifact_id": f"{decomposed}:{s.artifact_id}",
                "project_id": "프로젝트\u00e9",
            }
        )
        for s in frame(5)
    )


@pytest.fixture(autouse=True)
def fresh_memo() -> None:
    forget_remembered_frame()


def test_the_source_digest_is_byte_for_byte_the_canonical_one() -> None:
    shuffled = list(frame(40))
    random.Random(3).shuffle(shuffled)
    cases = {
        "empty": (),
        "one": frame(1),
        "unicode, decomposed Hangul and an accent": unicode_frame(),
        "large": frame(3000),
        "other order": tuple(shuffled),
        "reversed": tuple(reversed(frame(40))),
    }
    for name, evidence in cases.items():
        assert dense_source_digest(evidence) == reference_source_digest(evidence), name


def test_the_source_digest_depends_on_order_content_and_membership() -> None:
    evidence = frame(10)
    digests = {
        dense_source_digest(evidence),
        dense_source_digest(tuple(reversed(evidence))),
        dense_source_digest(evidence[:-1]),
        dense_source_digest(
            (*evidence[:-1], evidence[-1].model_copy(update={"text_sha256": "0" * 64}))
        ),
    }
    assert len(digests) == 4


def test_the_remembered_digest_is_used_only_for_the_very_same_spans() -> None:
    evidence = frame(30)
    first = dense_source_digest(evidence)
    assert remembered_frame() is evidence
    # The same objects in a new tuple hit the memory; the result is the one computed first.
    assert dense_source_digest(tuple(list(evidence))) == first
    assert remembered_frame() is evidence
    # An equal but different set of objects, a changed span and another order are computed again.
    copies = tuple(s.model_copy() for s in evidence)
    assert dense_source_digest(copies) == first == reference_source_digest(copies)
    assert remembered_frame() is not evidence
    changed = (*copies[:-1], copies[-1].model_copy(update={"text_sha256": "1" * 64}))
    assert dense_source_digest(changed) == reference_source_digest(changed) != first
    reordered = tuple(reversed(changed))
    assert dense_source_digest(reordered) == reference_source_digest(reordered)


def scores_for(values: list[float], spans: tuple[EvidenceSpan, ...]) -> tuple[DenseScore, ...]:
    return tuple(
        DenseScore(span_id=s.span_id, score=Decimal(f"{max(-1.0, min(1.0, v)):.9f}"))
        for s, v in zip(spans, values, strict=True)
    )


EDGE_VALUES = [
    -1.0,
    1.0,
    -1.5,
    1.5,
    0.0,
    -0.0,
    1e-13,
    -1e-13,
    0.0000000005,
    0.0000000015,
    0.1234567895,
    0.9999999995,
    -0.9999999995,
    0.9999999994,
    0.5,
    -0.5,
]


def test_prepared_scores_equal_validated_scores_including_the_edges() -> None:
    spans = tuple(span(i, f"a{i}") for i in range(len(EDGE_VALUES)))
    built = dense_scores(spans, EDGE_VALUES)
    checked = scores_for(EDGE_VALUES, spans)
    assert built == checked
    assert [b.model_dump_json() for b in built] == [c.model_dump_json() for c in checked]
    # Everything the constructor skipped would have passed validation.
    for score in built:
        assert DenseScore.model_validate(score.model_dump()) == score
    assert str(built[0].score) == "-1.000000000" and str(built[1].score) == "1.000000000"
    assert score_decimal(1.5) == Decimal("1.000000000") and score_decimal(-1.5) == Decimal(
        "-1.000000000"
    )


def test_a_prepared_material_equals_a_validated_one_and_has_the_same_digest() -> None:
    spans = frame(30)
    rng = random.Random(11)
    values = [rng.uniform(-1.2, 1.2) for _ in spans]
    prepared = DenseMaterial.prepared(
        project_id="p",
        question_sha256="a" * 64,
        source_digest="b" * 64,
        scores=dense_scores(spans, values),
    )
    checked = DenseMaterial(
        project_id="p",
        question_sha256="a" * 64,
        source_digest="b" * 64,
        scores=scores_for(values, spans),
    )
    assert prepared == checked
    assert prepared.model_dump_json() == checked.model_dump_json()
    assert canonical_payload(prepared) == canonical_payload(checked)
    assert dense_material_digest(prepared) == reference_material_digest(checked)


def test_the_material_digest_is_byte_for_byte_the_canonical_one() -> None:
    spans = unicode_frame()
    values = [0.25, -0.75, 0.0, 1.0, -1.0]

    def make(
        scores: tuple[DenseScore, ...] = (), reason: DenseUnavailableReason | None = None
    ) -> DenseMaterial:
        return DenseMaterial(
            project_id="프로젝트\u00e9",
            question_sha256="a" * 64,
            source_digest="b" * 64,
            scores=scores,
            unavailable_reason=reason,
        )

    cases = {
        "no scores": make(),
        "unavailable": make(reason="INDEX_STALE"),
        "unicode span ids": make(scores_for(values, spans)),
        "many": make(scores_for([i / 3000 - 0.5 for i in range(3000)], frame(3000))),
    }
    for name, material in cases.items():
        assert dense_material_digest(material) == reference_material_digest(material), name
