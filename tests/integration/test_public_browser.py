import socket
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.connectors.anonymous_browser import AnonymousChromiumReader
from thoth.adapters.connectors.common import utc_now
from thoth.adapters.connectors.public_reader import PublicUrlPolicy
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.adapters.connectors.registry import ConnectorRegistry
from thoth.adapters.storage.schema import artifacts, control_records, evidence_spans
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import ResearchSourcePlan, SourceSelector
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.web_acquisition import WebPage

URI = "https://fixture.example.org/research"
SHELL = b"""<html><head><title>Research fixture</title></head><body><div id="root"></div>
<script>document.getElementById('root').innerHTML=
'<p>Measured latency: 12 ms. Conditions: alpha.</p>';</script></body></html>"""


class FixtureReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        assert timeout > 0
        self.calls.append(uri)
        if uri != URI:
            raise ConnectorFailure(ConnectorErrorCode.EGRESS_DENIED, "FIXTURE_ONLY")
        assert len(SHELL) <= max_bytes
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=SHELL,
            retrieved_at=utc_now(),
        )


class SearchModel(ControlledResearchModel):
    def __init__(self) -> None:
        super().__init__()
        self.source_planner_calls = 0

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        model_request = request
        if model_request.role != ModelRole.SOURCE_PLANNER:
            return await super().structured(model_request)
        self.source_planner_calls += 1
        result = ResearchSourcePlan(
            selectors=(
                SourceSelector.model_validate(
                    {"connector_id": "public-fixture", "selector": {"mode": "READ", "uri": URI}}
                ),
            ),
            candidates=(),
            reason="Read the registered fixture",
        )
        return ModelResult(
            output=model_request.output_model.model_validate(result.model_dump()),
            model_id="CONTROLLED",
            prompt_version=model_request.prompt_version,
            scripted=True,
            input_digest="a" * 64,
            output_digest="b" * 64,
        )


@pytest.mark.asyncio
async def test_normal_research_renders_and_retains_http_provenance(tmp_path: Path):
    reader = FixtureReader()
    connector = PublicWebConnector(
        reader, connector_id="public-fixture", browser=AnonymousChromiumReader(reader)
    )
    runtime = await setup(
        tmp_path, SearchModel(), source=False, connector_registry=ConnectorRegistry((connector,))
    )
    try:
        policy = value(
            await runtime.bus.dispatch(
                request("project/policy/read", "policy", {"project_id": "p"})
            )
        )["policy"]
        project = value(
            await runtime.bus.dispatch(request("project/read", "project-read", {"project_id": "p"}))
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "allow-fixture",
                    {
                        "project_id": "p",
                        "expected_revision": project["revision"],
                        "payload": {
                            **policy["payload"],
                            "connector_allowlist": ["public-fixture"],
                            "connector_allowed_egress_classes": ["ALLOWLISTED_EXTERNAL"],
                            "max_query_egress_security_class": "INTERNAL",
                        },
                    },
                )
            )
        )
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "research",
                    {
                        "project_id": "p",
                        "problem": "웹에서 현재 지연 측정 자료를 확인해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read", "result", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        assert state["current_result"]["result"].get("discovery", {}).get("acquired_count") == 1, (
            state["current_result"]["phase"],
            state["current_result"]["result"].get("discovery"),
        )
        with runtime.ledger.engine.connect() as connection:
            saved = connection.execute(select(artifacts)).mappings().all()
            assert len(saved) == 2
            assert {row["parser_name"] for row in saved} == {"provenance-only", "html"}
            spans = connection.execute(select(evidence_spans.c.exact_text)).scalars().all()
            assert any("12 ms" in text for text in spans)
            assert not any("innerHTML" in text for text in spans)
            assert (
                connection.execute(
                    select(control_records).where(
                        control_records.c.record_type == "WEB_TRANSFORMATION"
                    )
                ).first()
                is not None
            )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_empty_connector_allowlist_stops_before_discovery(tmp_path: Path) -> None:
    reader = FixtureReader()
    model = SearchModel()
    connector = PublicWebConnector(
        reader, connector_id="public-fixture", browser=AnonymousChromiumReader(reader)
    )
    runtime = await setup(
        tmp_path, model, source=False, connector_registry=ConnectorRegistry((connector,))
    )
    try:
        policy = value(
            await runtime.bus.dispatch(
                request("project/policy/read", "policy", {"project_id": "p"})
            )
        )["policy"]
        assert policy["payload"]["connector_allowlist"] == []
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "research-no-route",
                    {
                        "project_id": "p",
                        "problem": "웹에서 현재 지연 측정 자료를 확인해줘",
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
                    "result-no-route",
                    {"project_id": "p", "thread_id": accepted["thread_id"]},
                )
            )
        )
        discovery = state["current_result"]["result"]["discovery"]
        assert discovery["reason"] == "NO_ALLOWED_DISCOVERY_ROUTE"
        assert discovery["acquired_count"] == 0
        assert model.source_planner_calls == 0
        assert reader.calls == []
    finally:
        runtime.close()


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_public_policy_rejects_private_dns(monkeypatch: pytest.MonkeyPatch, address: str):
    def addresses(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    with pytest.raises(ConnectorFailure, match="NONPUBLIC"):
        PublicUrlPolicy(("fixture.example.org",)).resolve(URI)
