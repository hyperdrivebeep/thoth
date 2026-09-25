"""Bind reranker IDs at the provider wire while retaining the independent consumer guard."""

import json
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from openai import AsyncOpenAI
from tests.unit.test_post_audit_contracts import span

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel, strict_output_schema
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.adapters.models.xai_model import XaiOAuthModel
from thoth.adapters.models.xai_oauth import OmoXaiSessionReader, XaiSession
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.application.services.research_context_assembler import assemble_context
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import EvidenceRanking, ReviewProposal
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelReceiveObservation,
    ModelTransportReply,
    OAuthSession,
    PreparedModelDispatch,
)
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _ranking_field(wire: dict[str, object]) -> dict[str, object]:
    body = _record(wire["text"])
    format = _record(body["format"])
    schema = _record(format["schema"])
    properties = _record(schema["properties"])
    return _record(properties["ordered_span_ids"])


def _prompt_text(wire: dict[str, object]) -> str:
    message = _record(_list(wire["input"])[0])
    content = _record(_list(message["content"])[0])
    return _text(content["text"])


class _CodexSession:
    def read(self) -> OAuthSession:
        return OAuthSession("fixture", "fixture", "fixture")


class _XaiSession(OmoXaiSessionReader):
    def __init__(self) -> None:
        super().__init__(root=None)

    def read(self) -> XaiSession:
        return XaiSession("fixture", "fixture")


class _FixtureBoundary:
    def check(self) -> None:
        pass

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        raise AssertionError("unexpected reserve")

    def transport(self, payload_bytes: int) -> None:
        raise AssertionError("unexpected transport")

    def call_timeout(self) -> None:
        return None

    def owns_attempt(self) -> bool:
        raise AssertionError("unexpected owns_attempt")

    def new_model_call(self) -> str:
        return "call:fixture"

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        pass

    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: ModelReceiveObservation | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        pass


class RecordingExecutor(CodexHttpExecutor):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(_CodexSession())
        self.responses = responses
        self.payloads: list[dict[str, object]] = []

    async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply:
        self.payloads.append(_record(json.loads(request.payload)))
        text = self.responses.pop(0)
        return ModelTransportReply(text, len(text.encode()), remote_stop="COMPLETED")


def ranking_request(count: int) -> ModelRequest[EvidenceRanking]:
    evidence = tuple(
        span(f"span:candidate-{i}", i + 1, f"Exact original measurement {i}") for i in range(count)
    )
    return ModelRequest(
        role=ModelRole.EVIDENCE_RERANKER,
        project_id="p",
        cutoff_at=datetime(2026, 9, 22, tzinfo=UTC),
        context_pack=ContextPack(
            case_id="c",
            project_id="p",
            object_id="o",
            problem="Rank candidates",
            evidence=evidence,
            criteria=(),
            sufficiency=None,
            input_head_set_digest="a" * 64,
        ),
        output_model=EvidenceRanking,
        prompt_version="fixture",
        model_policy_ref="policy:p",
        max_output_tokens=1000,
    )


@pytest.mark.parametrize("count", [0, 160])
async def test_actual_codex_http_wire_binds_every_candidate_and_empty_pool(count: int) -> None:
    request = ranking_request(count)
    ids = [s.span_id for s in request.context_pack.evidence]
    response = EvidenceRanking(ordered_span_ids=tuple(ids), rationale="Controlled valid ordering")
    executor = RecordingExecutor([response.model_dump_json()])
    result = await CodexOAuthModel(executor).structured(request)
    assert result.output == response and len(executor.payloads) == 1
    wire = executor.payloads[0]
    field = _ranking_field(wire)
    assert _record(field["items"]).get("enum", []) == ids
    if not ids:
        assert field["maxItems"] == 0
    prompt = _prompt_text(wire)
    evidence = _record(
        json.loads(prompt.split("UNTRUSTED_EVIDENCE_SPANS\n")[1].split("\n\nTASK\n")[0])
    )
    assert [_text(_record(s)["span_id"]) for s in _list(evidence["spans"])] == ids


async def test_existing_shape_repair_keeps_the_same_candidate_enum() -> None:
    request = ranking_request(3)
    response = EvidenceRanking(ordered_span_ids=("span:candidate-2",), rationale="Controlled reply")
    executor = RecordingExecutor(["not JSON", response.model_dump_json()])
    assert (await CodexOAuthModel(executor).structured(request)).output == response
    assert len(executor.payloads) == 2
    for wire in executor.payloads:
        field = _ranking_field(wire)
        assert _record(field["items"])["enum"] == [
            s.span_id for s in request.context_pack.evidence
        ]
    assert "REPAIR_TASK" in _prompt_text(executor.payloads[1])


@pytest.mark.parametrize(
    "ordered", [("span:not-a-candidate",), ("span:candidate-0", "span:candidate-0")]
)
async def test_nonconforming_provider_output_still_fails_the_consumer_guard(
    ordered: tuple[str, ...],
) -> None:
    request = ranking_request(2)
    invalid = EvidenceRanking(ordered_span_ids=ordered, rationale="Controlled protocol violation")
    executor = RecordingExecutor([invalid.model_dump_json()])
    result = await CodexOAuthModel(executor).structured(request)
    # The generic codec is intentionally distinct from the request-bound consumer check.
    assert result.output.ordered_span_ids == ordered
    with pytest.raises(ValueError, match="RERANK_UNKNOWN_OR_DUPLICATE_ID"):
        assemble_context(
            ordered,
            request.context_pack.evidence,
            request.context_pack.evidence,
            cast(ArtifactLedgerPort, None),  # Guard must reject IDs before ledger use.
        )
    assert len(executor.payloads) == 1  # no new repair/retry contract or silent ID conversion


def test_other_span_reference_constraints_remain_unchanged():
    ids = ("span:one", "span:two")
    schema = constrain_span_references(strict_output_schema(ReviewProposal), ids)
    fields = _record(_record(_record(schema["$defs"])["SemanticReviewCandidate"])["properties"])
    for name in ("evidence_refs", "applicability_basis"):
        assert _record(_record(fields[name])["items"])["enum"] == list(ids)


@pytest.mark.parametrize("count", [0, 160])
async def test_xai_prepared_wire_uses_the_same_ranking_constraint(count: int) -> None:
    request = ranking_request(count)
    ids = [s.span_id for s in request.context_pack.evidence]
    response = EvidenceRanking(ordered_span_ids=tuple(ids), rationale="Controlled output")
    payloads: list[dict[str, object]] = []

    class Executor(XaiResponsesExecutor):
        async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply:
            payloads.append(_record(json.loads(request.payload)))
            return ModelTransportReply(response.model_dump_json(), 1, remote_stop="COMPLETED")

    executor = Executor(_XaiSession())
    assert (await XaiOAuthModel(executor).structured(request)).output == response
    field = _ranking_field(payloads[0])
    assert _record(field["items"]).get("enum", []) == ids
    if not ids:
        assert field["maxItems"] == 0


@pytest.mark.parametrize("count", [0, 160])
async def test_openai_research_http_wire_uses_the_same_ranking_constraint(count: int) -> None:
    request = ranking_request(count)
    ids = [s.span_id for s in request.context_pack.evidence]
    response = EvidenceRanking(ordered_span_ids=tuple(ids), rationale="Controlled output")
    payloads: list[dict[str, object]] = []

    def reply(http_request: httpx.Request) -> httpx.Response:
        payloads.append(_record(json.loads(http_request.content)))
        return httpx.Response(
            200,
            json={
                "id": "response:fixture",
                "object": "response",
                "created_at": 0,
                "model": "fixture",
                "status": "completed",
                "usage": None,
                "output": [
                    {
                        "type": "message",
                        "id": "message:fixture",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": response.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    boundary = _FixtureBoundary()
    token = research_work.set(
        ResearchWork(
            RevisionRef(
                project_id="p",
                entity_type="THREAD",
                entity_id="request:fixture",
                revision_id="r",
                revision_digest="a" * 64,
            ),
            "fixture",
            boundary,
        )
    )
    try:
        async with AsyncOpenAI(
            api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))
        ) as client:
            assert (
                await OpenAIResponsesModel(client, model_id="fixture").structured(request)
            ).output == response
        field = _ranking_field(payloads[0])
        assert _record(field["items"]).get("enum", []) == ids
        if not ids:
            assert field["maxItems"] == 0
    finally:
        research_work.reset(token)
