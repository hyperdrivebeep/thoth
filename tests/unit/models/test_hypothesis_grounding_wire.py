"""Actual strict schema and shared prompt construction, without contacting a provider."""

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.hypothesis_grounding_transport import measured_wire
from tests.fixtures.hypothesis_grounding_wire import (
    fill_row,
    filler_payload,
    generator_request,
    raw_portfolio,
)

from thoth.adapters.models import strict_output_schema
from thoth.adapters.models.codex_oauth import prompt_envelope
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisTableFillV5
from thoth.domain.model import ModelRequest


def test_generator_wire_limits_are_not_narrower_than_v4() -> None:
    request = generator_request()
    old = strict_output_schema(HypothesisPortfolioV4)["$defs"]["ExpectedResultV4"]["properties"]
    new = strict_output_schema(request.output_model)["$defs"]["ReceivedExpectedResultV5"][
        "properties"
    ]
    assert old["basis"]["maxItems"] == new["basis"]["maxItems"] == 5
    assert old["expected"] == new["expected"]
    assert not {"source_bindings", "source_stage", "relation_assessment"} & new.keys()


def test_filler_wire_keeps_300_char_20_span_receive_envelope() -> None:
    old = strict_output_schema(HypothesisTableFillV4)["$defs"]["FilledRowV4"]["properties"]
    schema = strict_output_schema(ReceivedHypothesisTableFillV5)
    new = schema["$defs"]["ReceivedFilledRowV5"]["properties"]
    assert old["basis"]["maxItems"] == new["basis"]["maxItems"] == 20
    assert old["expected"] == new["expected"]
    received = ReceivedHypothesisTableFillV5.model_validate(
        filler_payload(fill_row("h2", "x" * 300, basis=[f"span:{i}" for i in range(20)]))
    )
    assert len(received.tests[0].rows[0].basis) == 20
    with pytest.raises(ValidationError):
        ReceivedHypothesisTableFillV5.model_validate(filler_payload(fill_row("h2", "x" * 301)))
    assert schema["$defs"]["ReceivedGroundingV1"]["properties"]["rationale"]["maxLength"] == 200


@pytest.mark.parametrize("stage", ["generator", "filler"])
def test_new_prompt_has_grounding_roles_and_explicit_authority_boundary(stage: str) -> None:
    request: ModelRequest[Any] = generator_request()
    if stage == "filler":
        request = replace(
            request,
            role=ModelRole.HYPOTHESIS_TABLE_FILLER,
            prompt_version="hypothesis_table_filler.v6",
            output_model=ReceivedHypothesisTableFillV5,
        )
    text = prompt_envelope(request)
    assert "PREDICTION_GROUNDING_V1" in text and "at most 200 characters" in text
    assert "HYPOTHESIS_ONLY uses basis=[]" in text
    assert 'may use rationale=""' in text
    assert "never a reviewed support judgement" in text
    assert "Do not output source versions, hashes" in text


@pytest.mark.asyncio
async def test_actual_http_serializer_keeps_reservation_and_does_not_claim_a_remote_cap() -> None:
    request = generator_request()
    data = await measured_wire(request, raw_portfolio({"h1": {"h1": "worse"}}))
    assert data["output_tokens_reserved"] == 6000
    assert data["provider_output_control"] == "OBSERVATION_ONLY"
    assert data["remote_max_output_tokens_present"] is False
    assert data["serialized_request_bytes"] > 0 and data["network_calls"] == 0
