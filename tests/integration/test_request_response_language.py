"""Normal RPC admission, queue and replay with controlled transports only."""

import asyncio
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup


@pytest.mark.asyncio
async def test_languages_are_frozen_per_request_in_one_thread(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        payload = {
            "project_id": "p",
            "contract_version": 2,
            "problem": "지연을 조사해줘",
            "response_language": "ko",
        }
        first = value(await runtime.bus.dispatch(request("thread/start", "ko", payload)))
        await asyncio.wait_for(model.started.wait(), 10)
        thread = first["thread_id"]
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "en",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "instruction": "Explain the next result",
                        "response_language": "en",
                    },
                )
            )
        )
        assert queued["status"] == "QUEUED_AFTER_CURRENT"
        assert model.calls[0].context_pack.research_context["response_language"] == "ko"
        conflict = await runtime.bus.dispatch(
            request(
                "thread/start",
                "ko",
                {
                    **payload,
                    "response_language": "en",
                },
            )
        )
        assert conflict.error is not None
        model.release.set()
        await runtime.bus.drain()
        status = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "read",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "view": "FULL",
                    },
                )
            )
        )
        assert status["request"]["schema_version"] == "2.1.0"
        assert status["request"]["response_language"] == "en"
        languages = [c.context_pack.research_context.get("response_language") for c in model.calls]
        assert "en" in languages and None not in languages
        first_en = languages.index("en")
        assert set(languages[:first_en]) == {"ko"}
        assert set(languages[first_en:]) == {"en"}
        assert runtime.bus.read_operation(first["operation_id"]).state.value == "SUCCEEDED"
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [None, "", "fr", 1])
async def test_invalid_explicit_language_does_not_start_research(tmp_path: Path, language):
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        reply = await runtime.bus.dispatch(
            request(
                "thread/start",
                "invalid",
                {
                    "project_id": "p",
                    "problem": "question",
                    "contract_version": 2,
                    "response_language": language,
                },
            )
        )
        assert reply.error is not None
        assert not model.calls
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unsealed_ephemeral_response_is_not_a_research_result(tmp_path: Path, monkeypatch):
    from thoth.application.commands.behavior_execution import BehaviorThreadEntry
    from thoth.protocol.deferred import EphemeralCommandResult

    async def ephemeral(self, payload):
        return EphemeralCommandResult(
            {"private_response": "synthetic-not-for-storage"}, {"ok": True}
        )

    monkeypatch.setattr(BehaviorThreadEntry, "analyze", ephemeral)
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        admission = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "ephemeral",
                    {
                        "project_id": "p",
                        "problem": "question",
                        "contract_version": 2,
                        "response_language": "en",
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(admission["operation_id"])
        assert operation.state.value == "FAILED"
        assert "NESTED_RESEARCH_ADMISSION_FORBIDDEN" in str(operation.error)
        assert "synthetic-not-for-storage" not in str(operation.model_dump())
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("before_language,after_language", [(None, "ko"), ("ko", "en")])
async def test_input_same_key_language_change_is_a_conflict_and_preserves_original(
    tmp_path: Path,
    before_language,
    after_language,
):
    from thoth.domain.canonical import canonical_payload
    from thoth.protocol.jsonrpc import RpcErrorCode

    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        payload = {
            "project_id": "p",
            "thread_id": started["thread_id"],
            "contract_version": 2,
            "instruction": "next question",
            **({"response_language": before_language} if before_language else {}),
        }
        accepted = value(await runtime.bus.dispatch(request("thread/input", "same-key", payload)))
        assert accepted["status"] == "QUEUED_AFTER_CURRENT"
        original = canonical_payload(runtime.bus.read_operation(accepted["operation_id"]))
        conflict = await runtime.bus.dispatch(
            request(
                "thread/input",
                "same-key",
                {
                    **payload,
                    "response_language": after_language,
                },
            )
        )
        assert conflict.error.code == RpcErrorCode.IDEMPOTENCY_CONFLICT
        assert canonical_payload(runtime.bus.read_operation(accepted["operation_id"])) == original
        assert len(model.calls) == 1
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()
