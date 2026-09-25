from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult

T = TypeVar("T", bound=BaseModel)


async def test_model_maps_sources_then_literal_natural_reply_resumes_same_inquiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with reference_harness(tmp_path) as h:
        original = DynamicA02Model.structured
        calls: list[str] = []

        async def mapped(instance: DynamicA02Model, request: ModelRequest[T]) -> ModelResult[T]:
            if request.role != ModelRole.REFERENCE_MAPPER:
                return await original(instance, request)
            calls.append(request.context_pack.problem)
            assert {m["span_id"] for m in h.request["measurements"]} <= {
                s.span_id for s in request.context_pack.evidence
            }
            proposal = (
                {
                    "answers": [
                        {"field": "denominator", "value": "per-request", "quote": "per-request"}
                    ]
                }
                if "Use per-request" in request.context_pack.problem
                else {
                    "measurements": h.request["measurements"],
                    "missing_field_order": ["denominator"],
                }
            )
            output = request.output_model.model_validate(proposal)
            return ModelResult(
                output=output,
                model_id="SCRIPTED_REFERENCE_MAPPER",
                prompt_version=request.prompt_version,
                scripted=True,
                input_digest=domain_digest(
                    "MODEL_INPUT", "1.0.0", canonical_payload(request.context_pack)
                ),
                output_digest=domain_digest("MODEL_OUTPUT", "1.0.0", canonical_payload(output)),
            )

        monkeypatch.setattr(DynamicA02Model, "structured", mapped)
        first = value(
            await h.input(
                "model-map",
                instruction="Map the selected observations and identify missing target conditions.",
                reference_request={
                    **h.request,
                    "measurements": None,
                    "target": {**h.request["target"], "denominator": None},
                },
            )
        )
        inquiry = first["reference_inquiry"]
        assert inquiry["state"] == "NEEDS_INPUT"
        assert len(inquiry["mapping_traces"]) == 1
        answer = value(
            await h.input("natural-answer", instruction="Use per-request as the denominator.")
        )
        after = answer["reference_inquiry"]
        assert after["inquiry_id"] == inquiry["inquiry_id"]
        assert after["state"] == "CALCULATED", after["reason_codes"]
        assert len(calls) == 2 and len(after["mapping_traces"]) == 2
        assert all(item["scripted"] for item in after["mapping_traces"])
        assert after["evaluator_input_allowed"] is False
        assert after["answers"][-1]["value"] == "per-request"
