import asyncio
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import request as rpc_request
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model

from thoth.adapters.storage.transaction import read_connection
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult

T = TypeVar("T", bound=BaseModel)


@pytest.mark.parametrize("kind", ["fabricated_answer", "cancel", "peer_revision"])
async def test_mapper_cannot_invent_answers_or_commit_after_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async with reference_harness(tmp_path) as h:
        initial = value(
            await h.input(
                "missing",
                reference_request={
                    **h.request,
                    "target": {**h.request["target"], "denominator": None},
                },
            )
        )
        before = await h.read()
        original = DynamicA02Model.structured
        called: list[bool] = []

        async def mapped(
            instance: DynamicA02Model, model_request: ModelRequest[T]
        ) -> ModelResult[T]:
            if model_request.role != ModelRole.REFERENCE_MAPPER:
                return await original(instance, model_request)
            with read_connection(h.runtime.ledger.engine) as connection:
                assert not connection.in_transaction(), "model call must not hold a DB transaction"
            called.append(True)
            if kind == "cancel":
                raise asyncio.CancelledError()
            if kind == "peer_revision":
                peer = create_runtime(tmp_path / "allowed")
                try:
                    value(
                        await peer.bus.dispatch(
                            rpc_request(
                                "criteria/field/correct",
                                "peer-correction",
                                {
                                    "project_id": h.project,
                                    "criterion_id": h.criterion["criterion_id"],
                                    "expected_revision_digest": before["revision_digest"],
                                    "field_path": "identity.name",
                                    "proposed_value": "Peer corrected criterion",
                                    "evidence_span_ids": h.request["source_refs"],
                                    "reason": "Concurrent human correction",
                                },
                            )
                        )
                    )
                finally:
                    peer.close()
            output = model_request.output_model.model_validate(
                {
                    "answers": [
                        {
                            "field": "denominator",
                            "value": "per-request",
                            "quote": "per-request",
                        }
                    ]
                }
            )
            return ModelResult(
                output=output,
                model_id="SCRIPTED_BOUNDARY",
                scripted=True,
                prompt_version=model_request.prompt_version,
                input_digest=domain_digest(
                    "MODEL_INPUT", "1.0.0", canonical_payload(model_request.context_pack)
                ),
                output_digest=domain_digest("MODEL_OUTPUT", "1.0.0", canonical_payload(output)),
            )

        monkeypatch.setattr(DynamicA02Model, "structured", mapped)
        if kind == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await h.input(
                    "cancelled-map",
                    instruction="Continue investigating while I check the denominator.",
                )
        else:
            result = await h.input(
                "forged-map", instruction="Continue investigating while I check the denominator."
            )
            assert result.error is not None
            reason = (
                "REFERENCE_MAPPING_BASIS_CHANGED"
                if kind == "peer_revision"
                else "REFERENCE_ANSWER_GROUNDING_MISSING"
            )
            assert reason in str(result.error)
        assert called == [True]
        after = await h.read("after-rejected-mapping")
        if kind == "peer_revision":
            assert after["revision_digest"] != before["revision_digest"]
            assert after["identity"]["name"] == "Peer corrected criterion"
            assert after["reference_inquiry"]["state"] == "STALE"
            assert after["reference_inquiry"]["answers"] == []
        else:
            assert after["revision_digest"] == before["revision_digest"]
            assert after["reference_inquiry"] == initial["reference_inquiry"]
