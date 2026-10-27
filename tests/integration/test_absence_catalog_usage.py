"""Large catalog checks retain all dependencies while recording source-owner and witness reads."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.absence_claim import AbsenceAssessments
from thoth.domain.canonical import model_digest
from thoth.domain.enums import AuthorityState, ModelRole
from thoth.domain.evidence_graph import SpanCorrectionRecord
from thoth.domain.model import ModelRequest, ModelResult


class CatalogClaimModel(ControlledResearchModel):
    def __init__(self, found: bool) -> None:
        super().__init__()
        self.target = "LAB-42" if found else "ABSENT_EXACT_LITERAL"

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        call = request
        result = await super().structured(call)
        if call.role != ModelRole.SEMANTIC_REVIEWER:
            return result
        data = result.output.model_dump(mode="json")
        statement = f"There is no {self.target}."
        data.update(
            answer=statement,
            absence_claims=[
                {
                    "statement": statement,
                    "target": self.target,
                    "target_language_basis": "SOURCE_LITERAL",
                    "kind": "LITERAL",
                    "claimed_scope": "ALL_MATERIAL",
                    "origin": "ANSWER",
                    "finding_index": None,
                }
            ],
        )
        output = call.output_model.model_validate(data)
        return replace(
            result,
            output=output,
            output_digest=model_digest("OUTPUT", output, schema_version="1.0.0"),
        )


@pytest.mark.parametrize("found", [False, True])
async def test_4200_spans_are_checked_completely_without_4200_resource_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    found: bool,
) -> None:
    model = CatalogClaimModel(found)
    runtime = await setup(tmp_path, model, source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "catalog.md").write_text(
            "\n".join(f"LAB-42 latency record {i}: 12 ms, condition alpha" for i in range(4200)),
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "connect",
                    {
                        "project_id": "p",
                        "relative_path": "catalog.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        host = research_host(runtime)
        corrections: list[str] = []
        original = host.analysis.evidence_graph.list_span_corrections

        def corrected(project: str, span: str) -> tuple[SpanCorrectionRecord, ...]:
            corrections.append(span)
            return original(project, span)

        monkeypatch.setattr(host.analysis.evidence_graph, "list_span_corrections", corrected)
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "What does LAB-42 say about latency?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "read",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "view": "FULL",
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED"
        manifest = state["current_result"]
        checks = AbsenceAssessments.model_validate(manifest["result"]["absence_assessments"])
        b = next(
            scope for scope in checks.scope_sets if scope.kind == "ALLOWED_EXTRACTED_AT_REQUEST"
        )
        assert len(b.members) == len(b.checked_refs) == 4200 and b.scan_complete
        assert checks.items[0].b_result.outcome == ("FOUND" if found else "NOT_FOUND_WITHIN_SCOPE")
        assert len(checks.items[0].b_result.witness_refs) == (1 if found else 0)
        source_ids = set(b.checked_refs)
        reads = {
            use["resource_ref"] for use in manifest["resource_uses"] if use["capability"] == "READ"
        }
        model_ids = {span.span_id for call in model.calls for span in call.context_pack.evidence}
        witnesses = {
            ref
            for item in checks.items
            for result in (item.a_result, item.b_result)
            for ref in result.witness_refs
        }
        assert reads & source_ids <= model_ids | witnesses
        assert witnesses <= reads
        assert {ref.artifact_id for ref in b.members} <= reads
        assert len(reads & source_ids) <= 162
        assert len(manifest["research_basis"]["source_basis"]) == 4200
        assert set(corrections) <= model_ids
        assert state["basis_currentness"]["state"] == "CURRENT"
        # All consumers of ResearchFreshnessService must handle the same catalog dependencies.
        value(
            await runtime.bus.query(
                request("project/review/list", "review", {"project_id": "p", "limit": 10})
            )
        )
        history = value(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "history",
                    {
                        "project_id": "p",
                        "scope": {"project_id": "p", "thread_id": started["thread_id"]},
                        "kinds": ["RESULT"],
                        "limit": 25,
                    },
                )
            )
        )
        assert history["items"]
        unselected = next(
            s for s in host.analysis.evidence("p") if s.span_id not in model_ids | witnesses
        )
        host.analysis.artifacts.update_evidence(
            unselected.model_copy(update={"authority_state": AuthorityState.NOT_ADMISSIBLE})
        )
        changed = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "changed-catalog",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                    },
                )
            )
        )
        assert changed["basis_currentness"]["state"] == "REVIEW_REQUIRED"
        assert "SOURCE_BASIS_CHANGED" in changed["basis_currentness"]["reasons"]
    finally:
        runtime.close()


async def test_version_catalog_rejects_other_project_and_independent_span_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
    from thoth.domain.resource_scope import (
        ResourceScopeError,
        current_resource_uses,
        resource_use_scope,
    )
    from thoth.ports.source_version_catalog import SourceVersionCatalogPort

    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        artifacts = research_host(runtime).analysis.artifacts
        assert isinstance(artifacts, SourceVersionCatalogPort)
        span = artifacts.list_evidence("p")[0]
        with resource_use_scope("p"):
            data = artifacts.read_evidence_version("p", span.artifact_id, span.source_version_id)
            assert data
            uses = current_resource_uses()
            assert uses is not None and len(uses) == 1 and uses[0].resource_ref == span.artifact_id
        with pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_PROJECT_MISMATCH"):
            artifacts.read_evidence_version("other", span.artifact_id, span.source_version_id)
        original = SqliteResourceScopeStore.read
        artifact_scope = original(
            SqliteResourceScopeStore(runtime.ledger.engine), "p", span.artifact_id
        )
        assert artifact_scope is not None

        def independent(self: SqliteResourceScopeStore, project: str, ref: str):
            return artifact_scope if ref == span.span_id else original(self, project, ref)

        monkeypatch.setattr(SqliteResourceScopeStore, "read", independent)
        with pytest.raises(ResourceScopeError, match="RESOURCE_CATALOG_SCOPE_NOT_UNIFORM"):
            artifacts.read_evidence_version("p", span.artifact_id, span.source_version_id)
    finally:
        runtime.close()
