from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from tests.integration.psyche_workspace_snapshot import clone_database_read_only

from thoth.application.services.research_retrieval import lexical_candidates
from thoth.apps.storage_composition import open_stores
from thoth.domain.behavior_policy import RetrievalBehaviorInput, select_evidence_context
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import connected_retrieval_spans
from thoth.domain.model import ContextPack
from thoth.domain.research_request import RevisionRef

WORKSPACE = Path(__file__).resolve().parents[2] / ".thoth-desktop-demo"
QUESTION = "Psyche 표 3 발사 준비 조건 4개가 원문에서 각각 확인되는지"


pytestmark = pytest.mark.skipif(
    not (WORKSPACE / "db" / "thoth.sqlite3").exists(),
    reason="live desktop workspace is not present",
)


def test_psyche_connected_sources_accepts_unknown_catalog_without_a_model(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    temporary = TemporaryDirectory(prefix="psyche-clone-", dir=tmp_path)
    clone_workspace = Path(temporary.name)
    assert clone_workspace.resolve(strict=True).is_relative_to(tmp_path.resolve(strict=True))
    request.addfinalizer(temporary.cleanup)
    cloned_database = clone_database_read_only(WORKSPACE, clone_workspace)
    assert cloned_database.is_relative_to(clone_workspace)
    stores = open_stores(clone_workspace)
    try:
        project = next(
            (
                item
                for item in stores.projects.list()
                if "psyche" in item.name.casefold() or "psyche" in item.project_id.casefold()
            ),
            None,
        )
        if project is None:
            pytest.skip("Psyche project is not in the live desktop workspace")
        active = {
            binding.artifact_id
            for binding in stores.governance.list_source_bindings(project.project_id)
            if binding.state == "ACTIVE"
        }
        catalog = tuple(
            span
            for span in stores.artifacts.list_evidence(project.project_id)
            if span.artifact_id in active
        )
        artifacts = {
            artifact.artifact_id: artifact.source_uri
            for artifact in stores.artifacts.list_artifacts(project.project_id)
            if artifact.artifact_id in active
        }
    finally:
        stores.close()

    assert len(catalog) > 1000
    assert set(span.cutoff_state for span in catalog) == {CutoffState.UNKNOWN_TIME}

    accepted = RetrievalBehaviorInput(problem=QUESTION, evidence=catalog)
    usable = connected_retrieval_spans(accepted.evidence)
    selected = select_evidence_context(problem=QUESTION, evidence=accepted.evidence)
    shortlist = lexical_candidates(QUESTION, (), accepted.evidence)
    pack = ContextPack(
        case_id="request:live-psyche",
        project_id=project.project_id,
        object_id="object:live-psyche",
        problem=QUESTION,
        evidence=shortlist,
        criteria=(),
        sufficiency=None,
        input_head_set_digest="c" * 64,
        research_context={
            "request_ref": RevisionRef(
                project_id=project.project_id,
                entity_type="THREAD",
                entity_id="thread:live-psyche",
                revision_id="revision:live-psyche",
                revision_digest="d" * 64,
            ).model_dump(mode="json")
        },
    )

    shortlist_uris = [artifacts.get(span.artifact_id, "") for span in shortlist]
    assert usable
    assert selected.selected
    assert pack.evidence
    assert any("irb" in uri.casefold() or uri.casefold().endswith(".pdf") for uri in shortlist_uris)
    assert not any("intent/tweet" in span.exact_text.casefold() for span in shortlist)
    counts = Counter(span.artifact_id for span in shortlist)
    assert len(counts) >= 2, counts
