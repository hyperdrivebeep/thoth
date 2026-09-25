import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.structured_source_fixture import StructuredFixtureParser, fixture_export
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.parsers.docling_structure import convert_structure
from thoth.adapters.parsers.registry import ParserRegistry
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.research_context_assembler import assemble_context
from thoth.apps.runtime import create_runtime
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.resource_scope import ResourceScopeError


class MultiProvenanceFixtureParser:
    name = "multi-provenance-layout-json"
    version = "1.2.0"
    media_types = frozenset({"application/json"})
    suffixes = frozenset({".json"})
    capabilities = frozenset({"TEXT"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        return convert_structure(
            artifact,
            json.loads(raw),
            parser_name=self.name,
            parser_version=self.version,
            configuration_digest="multi-provenance-fixture-config",
            asset_digest="no-model-assets",
        )


def _multi_provenance_export() -> dict[str, Any]:
    text = "first located PDF segment second located PDF segment"
    second_start = text.index("second")
    export = fixture_export()
    export["texts"] = [
        {
            "self_ref": "#/texts/0",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": text,
            "text": text,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 10, "t": 20, "r": 160, "b": 38, "coord_origin": "TOPLEFT"},
                    "charspan": [0, second_start - 1],
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 14, "t": 42, "r": 190, "b": 60, "coord_origin": "TOPLEFT"},
                    "charspan": [second_start, len(text)],
                },
            ],
        }
    ]
    export["tables"] = []
    return export


async def test_registered_parser_ingestion_structure_expansion_reparse_and_normal_question(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime = create_runtime(
        tmp_path,
        model_resolver=model,
        resource_scope_policy=fixture_scope_policy(),
        parser_registry=ParserRegistry((StructuredFixtureParser(),)),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "p",
                    {
                        "project_id": "p",
                        "name": "Structure fixture",
                        "cutoff_at": "2026-09-15T00:00:00Z",
                    },
                )
            )
        )
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "tables.json").write_text(
            json.dumps(fixture_export()), encoding="utf-8"
        )
        inputs: dict[str, object] = {
            "project_id": "p",
            "relative_path": "tables.json",
            "media_type": "application/json",
            "authority": "INFORMAL",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
            "parser_selection": {"required_capabilities": ["TABLE"]},
        }
        connected = value(
            await runtime.bus.dispatch(request("project/source/connect", "source", inputs))
        )
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        analysis = handler.__self__.analysis
        artifact = str(connected["artifact"]["artifact_id"])
        version = str(connected["source_version_id"])
        document = analysis.artifacts.read_structure("p", artifact, version)
        assert (
            document
            and document.capability_observation
            and document.capability_observation.source_version_id == version
        )
        spans = analysis.evidence("p")
        anchor = next(s for s in spans if s.exact_text == "32")
        assembly = assemble_context((anchor.span_id,), (anchor,), spans, analysis.artifacts)
        text = {s.exact_text for s in assembly.evidence}
        assert "Table 9. Response timing" in text and "Time (ms)" in text
        assert "Times use milliseconds; sample excludes warm-up." in text
        assert "Table 10. Independent quality" not in text and "0.91" not in text
        assert assembly.wave.new_information_count > 0
        with pytest.raises(ResourceScopeError):
            analysis.artifacts.read_structure("other", artifact, version)
        assert analysis.artifacts.read_structure("p", artifact, "another-version") is None
        second = value(
            await runtime.bus.dispatch(request("project/source/connect", "reparse", inputs))
        )
        assert (
            second["source_version_id"] != version
            and analysis.artifacts.read_structure("p", artifact, version) == document
        )
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "question",
                    {
                        "project_id": "p",
                        "problem": "Compare alpha latency 32 ms with its caption and units. "
                        "Only connected sources.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        response = await runtime.bus.query(
            request(
                "thread/read", "result", {"project_id": "p", "thread_id": admitted["thread_id"]}
            )
        )
        if response.error is not None:
            await handler.__self__.read({"project_id": "p", "thread_id": admitted["thread_id"]})
        state = value(response)
        assert state["current_result"]["terminal_reason"] == "BOUNDED_RESEARCH_COMPLETE", state.get(
            "operation_error"
        )
        assert state["current_result"]["result"]["answer"]
        assert model.calls[0].context_pack.research_context["context_bundle"]
        assert [s["role"] for s in state["completed_stages"]][:4] == [
            "RESEARCH_PLANNER",
            "EVIDENCE_RERANKER",
            "SEMANTIC_REVIEWER",
            "REVIEW_ADJUDICATOR",
        ]
        assert state["answer_outcome"]["scientific_truth_certified"] is False
        assert state["resume_information"]["same_attempt_after_terminal_supported"] is False
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_multi_provenance_source_connect_context_and_detach(tmp_path: Path) -> None:
    runtime = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
        parser_registry=ParserRegistry((MultiProvenanceFixtureParser(),)),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "p",
                    {
                        "project_id": "p",
                        "name": "Multi provenance fixture",
                        "cutoff_at": "2026-09-15T00:00:00Z",
                    },
                )
            )
        )
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "multi-provenance.json").write_text(
            json.dumps(_multi_provenance_export()), encoding="utf-8"
        )
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "connect",
                    {
                        "project_id": "p",
                        "relative_path": "multi-provenance.json",
                        "media_type": "application/json",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "parser_selection": {"parser_name": "multi-provenance-layout-json"},
                    },
                )
            )
        )
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        analysis = handler.__self__.analysis
        artifact = str(connected["artifact"]["artifact_id"])
        version = str(connected["source_version_id"])
        document = analysis.artifacts.read_structure("p", artifact, version)
        assert document is not None
        material = [node for node in document.nodes if node.text]
        assert [node.text for node in material] == [
            "first located PDF segment",
            "second located PDF segment",
        ]
        located_ranges = [
            (node.locator.page, node.locator.char_start, node.locator.char_end)
            for node in material
        ]
        assert located_ranges == [
            (1, 0, 25),
            (2, 26, 52),
        ]
        assert material[1].relations[0].kind == "CONTINUATION_OF"
        spans = analysis.evidence("p")
        anchor = next(span for span in spans if span.exact_text == "second located PDF segment")
        assembly = assemble_context((anchor.span_id,), (anchor,), spans, analysis.artifacts)
        assert {span.exact_text for span in assembly.evidence} == {
            "first located PDF segment",
            "second located PDF segment",
        }
        assert assembly.bundles[0].container_id == material[0].node_id

        sources = value(
            await runtime.bus.dispatch(
                request("project/source/list", "sources", {"project_id": "p"})
            )
        )
        assert sources["bindings"][0]["state"] == "ACTIVE"
        detached = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "detach",
                    {
                        "project_id": "p",
                        "binding_id": connected["binding"]["binding_id"],
                        "mode": "DETACH",
                        "expected_project_revision": sources["cutoff_basis"]["project_revision"],
                    },
                )
            )
        )
        assert detached["binding"]["state"] == "DETACHED"
    finally:
        runtime.close()
