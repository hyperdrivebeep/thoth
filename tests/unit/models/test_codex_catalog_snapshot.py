"""Codex model list: kept when a refresh fails, stored per account, read without the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.models.test_codex_isolated_broker import (
    FakeAppServer,
    _auth,  # pyright: ignore[reportPrivateUsage]
    make_synthetic_broker,
)

from thoth.adapters.models.catalog import CodexModelCatalog
from thoth.adapters.models.catalog_store import CatalogSnapshotStore
from thoth.adapters.models.codex_broker import CodexAuthBroker
from thoth.adapters.models.codex_profile import PINNED_VERSION, CodexProfile, CodexProfileHold
from thoth.domain.model_catalog import authority_digest


def listing(server: FakeAppServer, entries: list[dict[str, object]]) -> None:
    original = server.call

    def call(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "model/list":
            server.methods.append(method)
            return {"data": entries, "nextCursor": None}
        return original(method, params)

    server.call = call  # type: ignore[method-assign]


def entry(
    model: str, efforts: tuple[str, ...] = ("low", "medium"), **more: object
) -> dict[str, object]:
    return {
        "model": model,
        "hidden": False,
        "isDefault": False,
        "defaultReasoningEffort": efforts[0],
        "supportedReasoningEfforts": [{"reasoningEffort": item} for item in efforts],
        **more,
    }


def fail_listing(server: FakeAppServer) -> None:
    original = server.call

    def call(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "model/list":
            server.methods.append(method)
            raise CodexProfileHold("CATALOG_UNAVAILABLE")
        return original(method, params)

    server.call = call  # type: ignore[method-assign]


def restarted(broker: CodexAuthBroker, calls: list[str]) -> CodexAuthBroker:
    def factory(profile: CodexProfile, _identity: object) -> FakeAppServer:
        calls.append("client")
        return FakeAppServer(profile)

    return CodexAuthBroker(
        broker.profile.workspace,
        client_factory=factory,
        version_runner=lambda _: PINNED_VERSION,
    )


def test_the_models_the_list_leaves_out_are_kept_with_their_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    listing(
        server,
        [
            entry("synthetic-codex", isDefault=True),
            entry("gpt-5.4"),
            entry("router/gpt-5.5"),
            entry("effort-only-unknown", ("ultra",)),
            entry("hidden-one", hidden=True),
        ],
    )
    state = broker.state(force=True)
    assert [o.model for o in state.options] == ["synthetic-codex"]
    assert state.options[0].entitlement == "PROVIDER_LISTED"
    snapshot = broker.catalog_snapshot()
    assert (
        snapshot is not None and snapshot.status == "ACTIVE" and snapshot.source == "PROVIDER_LIST"
    )
    assert {(item.model, item.reason) for item in snapshot.excluded} == {
        ("gpt-5.4", "UNSUPPORTED_SLUG"),
        ("router/gpt-5.5", "NAMESPACED_ID"),
        ("effort-only-unknown", "UNKNOWN_EFFORT_ONLY"),
    }
    assert snapshot.default_model == "synthetic-codex"


def test_a_failed_refresh_keeps_the_last_list_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    before = broker.state().options
    fail_listing(server)
    state = broker.state(force=True)
    assert state.options == before
    assert state.connected and state.execution_eligible
    snapshot = broker.catalog_snapshot()
    assert snapshot is not None
    assert snapshot.status == "STALE_LAST_GOOD" and snapshot.failure_reason == "CATALOG_UNAVAILABLE"
    assert snapshot.last_attempt_at is not None
    assert broker.session(None).model == "synthetic-codex"


def test_without_any_good_list_the_catalog_is_unavailable_but_the_login_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    for item in CatalogSnapshotStore(broker.profile.workspace).root.glob("*.json"):
        item.unlink()
    broker._catalog = None  # pyright: ignore[reportPrivateUsage]
    fail_listing(server)
    state = broker.state(force=True)
    assert state.options == () and state.connected is True
    assert state.reason_code == "CATALOG_UNAVAILABLE" and state.execution_eligible is False


def test_after_a_restart_the_saved_list_shows_without_asking_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    calls: list[str] = []
    again = restarted(broker, calls)
    options = CodexModelCatalog(broker.profile.workspace, broker=again).options()
    assert [o.model for o in options] == ["synthetic-codex"]
    assert calls == []


def test_another_account_does_not_get_the_previous_accounts_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    _auth(broker.profile, account="account:someone-else")
    calls: list[str] = []
    again = restarted(broker, calls)
    assert CodexModelCatalog(broker.profile.workspace, broker=again).options() == ()
    assert calls == []


def test_a_lost_login_stops_execution_but_keeps_the_saved_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    broker.profile.auth_path.unlink()
    again = restarted(broker, [])
    state = again.state(force=True)
    assert state.execution_eligible is False and state.reason_code == "LOGIN_REQUIRED"
    assert CodexModelCatalog(broker.profile.workspace, broker=again).options() == ()
    assert list(CatalogSnapshotStore(broker.profile.workspace).root.glob("*.json"))
    _auth(broker.profile)
    assert again.state().execution_eligible is True
    assert [
        o.model for o in CodexModelCatalog(broker.profile.workspace, broker=again).options()
    ] == ["synthetic-codex"]


def test_a_damaged_saved_list_is_ignored_and_the_next_refresh_writes_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    store = CatalogSnapshotStore(broker.profile.workspace)
    (path,) = store.root.glob("*.json")
    path.write_text("garbage", encoding="utf-8")
    again = restarted(broker, [])
    assert CodexModelCatalog(broker.profile.workspace, broker=again).options() == ()
    assert list(store.root.glob("*.corrupt*"))
    assert again.state(force=True).execution_eligible is True
    assert json.loads(path.read_text(encoding="utf-8"))["options"][0]["model"] == "synthetic-codex"


def test_only_the_account_digest_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    (path,) = CatalogSnapshotStore(broker.profile.workspace).root.glob("*.json")
    assert path.name == f"codex-oauth-{authority_digest('codex-oauth', 'account:synthetic')}.json"
    assert "account:synthetic" not in path.read_text(encoding="utf-8")


def test_a_refusal_is_marked_and_cleared_when_the_model_is_listed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    broker.record_execution("synthetic-codex", "REJECTED", "OAUTH_MODEL_NOT_AVAILABLE")
    catalog = CodexModelCatalog(broker.profile.workspace, broker=broker)
    assert catalog.options()[0].execution == "REJECTED"
    assert broker.state().options[0].execution == "REJECTED"
    # no other model is chosen for the user
    assert broker.session(None).model == "synthetic-codex"
    broker.state(force=True)
    assert catalog.options()[0].execution == "UNVERIFIED"


def test_a_success_is_marked_and_survives_a_refresh_that_still_lists_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    broker.record_execution("synthetic-codex", "VERIFIED", None)
    broker.state(force=True)
    assert (
        CodexModelCatalog(broker.profile.workspace, broker=broker).options()[0].execution
        == "VERIFIED"
    )


def test_asking_for_the_state_does_not_fetch_the_list_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    assert server.methods.count("model/list") == 1
    broker._cached_at = 0.0  # pyright: ignore[reportPrivateUsage]
    broker.state()
    assert server.methods.count("model/list") == 1
    broker.state(force=True)
    assert server.methods.count("model/list") == 2


def test_the_startup_check_asks_only_when_signed_in_and_the_list_is_missing_or_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    catalog = CodexModelCatalog(broker.profile.workspace, broker=broker)
    now, day = datetime.now(UTC), timedelta(hours=24)
    assert catalog.refresh_due(now, day) is False
    assert catalog.refresh_due(now + timedelta(hours=25), day) is True
    for item in CatalogSnapshotStore(broker.profile.workspace).root.glob("*.json"):
        item.unlink()
    broker._catalog = None  # pyright: ignore[reportPrivateUsage]
    assert catalog.refresh_due(now, day) is True
    broker.profile.auth_path.unlink()
    assert catalog.refresh_due(now, day) is False
