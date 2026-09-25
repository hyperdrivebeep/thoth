from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime import create_runtime
from thoth.domain.model_settings import ModelOption, ModelSelection, ResolvedModelSettings


def catalog(effort: str = "medium") -> StaticModelCatalog:
    return StaticModelCatalog(
        (
            ModelOption(
                provider="registered",
                model="example",
                reasoning_efforts=("low", "medium", "high"),
                default_effort="medium",
                capability_source="controlled-test",
            ),
        ),
        ModelSelection(provider="registered", model="example", reasoning_effort=effort),
    )


@pytest.mark.asyncio
async def test_settings_are_frozen_at_admission_and_replay_after_default_changes(tmp_path: Path):
    model = ControlledResearchModel()
    initial = await setup(tmp_path, model, source=False)
    initial.close()
    runtime = create_runtime(tmp_path, model_resolver=model, model_catalog=catalog())
    req = request(
        "thread/start",
        "start",
        {
            "project_id": "p",
            "problem": "Investigate",
            "contract_version": 2,
            "reasoning_effort": "high",
        },
    )
    # Commit admission without scheduling the worker, simulating T1 -> process loss.
    from thoth.protocol.bus import DispatchTicket
    from thoth.protocol.deferred import current_operation

    ticket = runtime.bus.claim(req)
    assert isinstance(ticket, DispatchTicket)
    token = current_operation.set(ticket.operation)
    try:
        admitted = await ticket.handler(ticket.request.params.input)
    finally:
        current_operation.reset(token)
    assert admitted is not None
    runtime.close()
    runtime = create_runtime(tmp_path, model_resolver=model, model_catalog=catalog("low"))
    try:
        accepted = value(await runtime.bus.dispatch(req))
        await runtime.bus.drain()
        assert model.calls
        assert all(
            call.model_settings is not None and call.model_settings.reasoning_effort == "high"
            for call in model.calls
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        assert result["request"]["model_settings"]["reasoning_effort"] == "high"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_local_credential_rpc_returns_json_without_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.models.local_credentials import LocalModelCredentials

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex"))
    def fake_account_connections(_self: LocalModelCredentials) -> list[dict[str, object]]:
        return [{"provider": "openai", "connected": False}]

    monkeypatch.setattr(
        LocalModelCredentials,
        "account_connections",
        fake_account_connections,
    )
    runtime = create_runtime(tmp_path)
    try:
        recorded = value(
            await runtime.bus.dispatch(
                request(
                    "model/credential/register",
                    "register-test-key",
                    {
                        "project_id": "system:workspace",
                        "provider": "openai",
                        "model": "gpt-test",
                        "api_key": "sk-not-a-real-key",
                        "base_url": "https://api.openai.com/v1",
                    },
                )
            )
        )
        assert recorded["credential"]["provider"] == "openai"
        assert "sk-not-a-real-key" not in str(recorded)
        listed = value(
            await runtime.bus.query(
                request(
                    "model/credential/list",
                    "list-test-key",
                    {"project_id": "system:workspace"},
                )
            )
        )
        assert listed["credentials"][0]["model"] == "gpt-test"
        assert "sk-not-a-real-key" not in str(listed)
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated_default", [False, True])
async def test_tui_selects_persists_and_rejects_unsupported_effort(
    tmp_path: Path, unrelated_default: bool
):
    model = ControlledResearchModel()
    initial = await setup(tmp_path, model, source=False)
    initial.close()
    selected_catalog = catalog()
    if unrelated_default:
        selected_catalog = StaticModelCatalog(
            selected_catalog.options(),
            ModelSelection(
                provider="registered", model="other-provider/unregistered", reasoning_effort="high"
            ),
        )
    runtime = create_runtime(tmp_path, model_resolver=model, model_catalog=selected_catalog)
    try:
        tui = TuiSessionService(
            session_id="settings",
            store=SqliteConversationSessionStore(runtime.ledger.engine),
            router=ConversationRouter(),
            dispatcher=BusConversationDispatcher(runtime.bus),
            clock=SystemClock(),
        )
        selected = await tui.execute("/model registered example high --project")
        assert selected.status.value == "DISPATCHED", selected
        assert (
            ResolvedModelSettings.model_validate(
                selected.response["effective_settings"]
            ).reasoning_effort
            == "high"
        )
        rejected = await tui.execute("/reasoning ultra --project")
        assert rejected.status.value == "HOLD"
        state = await tui.execute("/model")
        assert (
            ResolvedModelSettings.model_validate(
                state.response["effective_settings"]
            ).reasoning_effort
            == "high"
        )
        changed = await tui.execute("/reasoning low --project")
        assert changed.status.value == "DISPATCHED", changed
        assert (
            ResolvedModelSettings.model_validate(changed.response["effective_settings"]).model
            == "example"
        )
        await tui.execute("Check current conditions")
        await runtime.bus.drain()
        assert model.calls and all(
            c.model_settings is not None and c.model_settings.reasoning_effort == "low"
            for c in model.calls
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_provider_change_does_not_inherit_previous_effort(tmp_path: Path) -> None:
    from thoth.domain.model_settings import ModelOption, ModelSelection

    selected = StaticModelCatalog(
        (
            ModelOption(
                provider="codex-oauth",
                model="gpt-5.6-terra",
                reasoning_efforts=("low", "medium", "high", "xhigh"),
                default_effort="medium",
                capability_source="codex",
            ),
            ModelOption(
                provider="xai",
                model="grok-4.6",
                reasoning_efforts=("low", "medium", "high", "xhigh"),
                default_effort="high",
                capability_source="xai",
            ),
            ModelOption(
                provider="xai",
                model="grok-4.5",
                reasoning_efforts=("low", "medium", "high"),
                default_effort="high",
                capability_source="xai",
            ),
        ),
        ModelSelection(provider="codex-oauth", model="gpt-5.6-terra", reasoning_effort="xhigh"),
    )
    model = ControlledResearchModel()
    initial = await setup(tmp_path, model, source=False)
    initial.close()
    runtime = create_runtime(tmp_path, model_resolver=model, model_catalog=selected)
    try:
        tui = TuiSessionService(
            session_id="xai-switch",
            store=SqliteConversationSessionStore(runtime.ledger.engine),
            router=ConversationRouter(),
            dispatcher=BusConversationDispatcher(runtime.bus),
            clock=SystemClock(),
        )
        switched = await tui.execute("/model xai grok-4.6 --project")
        assert switched.status.value == "DISPATCHED", switched
        effective = ResolvedModelSettings.model_validate(switched.response["effective_settings"])
        assert effective.provider == "xai"
        assert effective.model == "grok-4.6"
        assert effective.reasoning_effort == "high"
        rejected = await tui.execute("/model xai grok-4.5 xhigh --project")
        assert rejected.status.value == "HOLD"
    finally:
        runtime.close()
