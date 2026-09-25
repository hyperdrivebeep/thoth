"""Large authorized catalogs do not become unused per-span correction reads."""

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_memory_basis import Boundary
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.research_retrieval import lexical_candidates
from thoth.domain.evidence_graph import SpanCorrectionRecord
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.domain.resource_scope import current_resource_uses, resource_use_scope


async def test_large_catalog_assembly_only_reads_structure_then_selected_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox/catalog.md").write_text(
            "\n".join(f"LAB-42 latency record {i}: 12 ms, condition alpha" for i in range(4200)),
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "large-catalog",
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
        calls: list[str] = []
        original = host.analysis.evidence_graph.list_span_corrections

        def observed(project: str, span: str) -> tuple[SpanCorrectionRecord, ...]:
            calls.append(span)
            return original(project, span)

        monkeypatch.setattr(host.analysis.evidence_graph, "list_span_corrections", observed)
        with resource_use_scope("p"):
            source = host.analysis.evidence("p")
            assert len(source) == 4200
            shortlist = lexical_candidates("LAB-42 latency", (), source)
            work = ResearchWork(
                RevisionRef(
                    project_id="p",
                    entity_type="THREAD",
                    entity_id="request:fixture",
                    revision_id="request:fixture",
                    revision_digest="a" * 64,
                ),
                "LAB-42 latency",
                Boundary(),
            )
            assembled = host.analysis.assemble(
                work, tuple(item.span_id for item in shortlist), shortlist, source
            )
            assert assembled.evidence and calls == []
            host.analysis.source_digest(assembled.evidence)
            assert set(calls) == {span.span_id for span in assembled.evidence}
            uses = current_resource_uses()
            assert uses is not None and len(uses) < 4096
            assert {span.span_id for span in assembled.evidence} <= {
                use.resource_ref for use in uses
            }
        assert model.calls == []
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "normal-large-entry",
                    {
                        "project_id": "p",
                        "problem": "What does LAB-42 say about latency?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation_id = accepted["operation_id"]
        assert isinstance(operation_id, str)
        terminal = runtime.bus.read_operation(operation_id)
        assert terminal is not None
        assert terminal.state.value == "SUCCEEDED", terminal.error
        assert model.calls  # controlled dispatch is reached; this is not a live provider claim
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "normal-large-read",
                    {"project_id": "p", "thread_id": accepted["thread_id"], "contract_version": 2},
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED"
        assert len(host.analysis.evidence("p")) == 4200
    finally:
        runtime.close()
