from thoth.application.services.research_retrieval import lexical_candidates
from thoth.domain.artifact import SourceLocator
from thoth.domain.behavior_policy import RetrievalBehaviorInput
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans


def _span(state: CutoffState) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"span:{state.value}",
        project_id="project:t",
        artifact_id="artifact:t",
        source_version_id="source-version:t",
        locator=SourceLocator(line=1),
        exact_text="alpha accuracy",
        text_sha256="a" * 64,
        extraction_method="text:1",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=state,
    )


def test_unknown_and_after_are_not_consumed() -> None:
    evidence = (
        _span(CutoffState.UNKNOWN_TIME),
        _span(CutoffState.AFTER_CUTOFF),
        _span(CutoffState.ELIGIBLE),
    )
    selected = connected_retrieval_spans(evidence)
    assert [item.cutoff_state for item in selected] == [CutoffState.ELIGIBLE]
    candidates = lexical_candidates("accuracy", ("accuracy",), evidence)
    assert all(item.cutoff_state is CutoffState.ELIGIBLE for item in candidates)


def test_unknown_connected_catalog_is_used_when_nothing_is_eligible() -> None:
    evidence = (
        _span(CutoffState.UNKNOWN_TIME),
        _span(CutoffState.AFTER_CUTOFF),
        _span(CutoffState.PROHIBITED_CONTEXT),
    )
    selected = connected_retrieval_spans(evidence)
    assert [item.cutoff_state for item in selected] == [CutoffState.UNKNOWN_TIME]
    candidates = lexical_candidates("accuracy", ("accuracy",), evidence)
    assert candidates
    assert all(item.cutoff_state is CutoffState.UNKNOWN_TIME for item in candidates)


def test_retrieval_input_accepts_connected_catalog_larger_than_one_thousand() -> None:
    evidence = tuple(
        _span(CutoffState.UNKNOWN_TIME).model_copy(update={"span_id": f"span:{index}"})
        for index in range(1001)
    )
    accepted = RetrievalBehaviorInput(problem="accuracy", evidence=evidence)
    assert len(accepted.evidence) == 1001


def test_connected_sources_path_accepts_unknown_catalog_and_dumped_request_ref() -> None:
    from thoth.domain.behavior_policy import select_evidence_context
    from thoth.domain.model import ContextPack
    from thoth.domain.research_request import RevisionRef

    evidence = tuple(
        _span(CutoffState.UNKNOWN_TIME).model_copy(update={"span_id": f"span:{index}"})
        for index in range(1676)
    )
    accepted = RetrievalBehaviorInput(
        problem="Psyche 표 3 발사 준비 조건 4개가 원문에서 각각 확인되는지",
        evidence=evidence,
    )
    selected = select_evidence_context(problem=accepted.problem, evidence=accepted.evidence)
    shortlist = lexical_candidates(accepted.problem, (), accepted.evidence)
    request_ref = RevisionRef(
        project_id="project:t",
        entity_type="THREAD",
        entity_id="thread:t",
        revision_id="revision:t",
        revision_digest="a" * 64,
    )
    pack = ContextPack(
        case_id="request:revision:t",
        project_id="project:t",
        object_id="object:t",
        problem=accepted.problem,
        evidence=shortlist,
        criteria=(),
        sufficiency=None,
        input_head_set_digest="b" * 64,
        research_context={"request_ref": request_ref.model_dump(mode="json")},
    )
    assert selected.selected
    assert pack.evidence
    assert all(span.cutoff_state is CutoffState.UNKNOWN_TIME for span in pack.evidence)


def test_lexical_shortlist_does_not_fill_with_one_source_of_web_chrome() -> None:
    chrome = tuple(
        _span(CutoffState.UNKNOWN_TIME).model_copy(
            update={
                "span_id": f"span:web-{index}",
                "artifact_id": "artifact:web",
                "exact_text": (
                    "*   [](https://x.com/intent/tweet?via=NASA&text=NASA%203%20Psyche)"
                    if index % 2
                    else "---"
                ),
            }
        )
        for index in range(200)
    )
    pdf = _span(CutoffState.UNKNOWN_TIME).model_copy(
        update={
            "span_id": "span:pdf-lrd",
            "artifact_id": "artifact:pdf",
            "locator": SourceLocator(page=12, line=4),
            "exact_text": (
                "Complete confirmation of all days in selected launch period; "
                "establish Launch Readiness Date (LRD) for Psyche."
            ),
        }
    )
    selected = lexical_candidates(
        "Psyche 표 3 발사 준비 조건 4개가 원문에서 각각 확인되는지",
        (),
        (*chrome, pdf),
    )
    assert any(span.span_id == "span:pdf-lrd" for span in selected)
    assert not any(span.exact_text.strip() in {"---"} for span in selected)
    assert not any("intent/tweet" in span.exact_text for span in selected)
