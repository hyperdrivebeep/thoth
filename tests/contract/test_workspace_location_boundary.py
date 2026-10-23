"""Injected support policy at real CLI/HTTP/store boundaries; no server or sync client starts."""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from thoth import cli
from thoth.adapters.storage import bundle
from thoth.adapters.storage import workspace_location as policy
from thoth.adapters.storage.workspace_setup import FilesystemWorkspaceSetup
from thoth.application.commands.workspace_setup import WorkspaceSetupHandlers
from thoth.apps import workspace_guard
from thoth.apps.storage_composition import open_stores
from thoth.apps.workspace_location_presentation import location_detail, workspace_location_payload
from thoth.apps.workspace_paths import workspace_id
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.storage_context import storage_deployment_mode, storage_mode_scope
from thoth.domain.workspace_location import LocationKind, WorkspaceLocation

http_app = importlib.import_module("thoth.adapters.http.app")


@pytest.fixture
def location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    state: dict[str, Any] = {"kind": "UNKNOWN", "reasons": ("SYNTHETIC_LOCATION",), "calls": 0}
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.chdir(tmp_path)

    def inspect(path: Path, **kwargs: object) -> WorkspaceLocation:
        state["calls"] += 1
        return WorkspaceLocation(
            state["kind"], state["reasons"], str(path.resolve()), workspace_id(path)
        )

    monkeypatch.setattr(policy, "inspect_workspace_location", inspect)
    return state


@pytest.mark.parametrize("kind,code", [("KNOWN_SYNC", 4), ("UNKNOWN", 5)])
@pytest.mark.parametrize(
    "command", ["shell", "run", "rpc", "events", "run-pack", "serve", "desktop"]
)
def test_cli_refuses_before_database_lock_or_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: dict[str, Any],
    kind: str,
    code: int,
    command: str,
) -> None:
    location["kind"] = kind
    monkeypatch.setattr(workspace_guard, "_interactive_confirmation", lambda: False)
    root = tmp_path / "blocked"
    args = [] if command == "shell" else [command]
    if command == "events":
        args += ["--project-id", "project:location", "--operation-id", "operation:location"]
    if command == "run":
        source = tmp_path / "request.json"
        source.write_text("{}", encoding="utf-8")
        args += ["--request", str(source)]
    if command == "run-pack":
        pack = tmp_path / "pack"
        pack.mkdir()

        def fake_pack(*args: object, **kwargs: object) -> object:
            return object()

        monkeypatch.setattr(cli, "load_project_pack", fake_pack)
        args += ["--pack", str(pack)]
    result = CliRunner().invoke(cli.app, [*args, "--workspace", str(root)])
    assert result.exit_code == code, result.output
    assert location["calls"] >= 1
    assert "THOTH" in result.output
    assert "작업 공간" in result.output if code == 4 else "확인하지 못했습니다" in result.output
    assert (
        "cannot work safely" in result.output if code == 4 else "accept-location" in result.output
    )
    assert not root.exists()


def test_confirm_forget_and_workspace_info_do_not_open_database(
    tmp_path: Path, location: dict[str, Any]
) -> None:
    root = tmp_path / "unknown"
    runner = CliRunner()
    preview = runner.invoke(cli.app, ["workspace", "--workspace", str(root)])
    assert preview.exit_code == 0 and "UNKNOWN" in preview.output and not root.exists()
    accepted = runner.invoke(cli.app, ["accept-location", "--workspace", str(root)])
    assert accepted.exit_code == 0, accepted.output
    assert policy.check_workspace_location(root).confirmed
    assert list(root.iterdir()) == [root / policy.CONFIRMATION_FILE]
    forgotten = runner.invoke(cli.app, ["accept-location", "--workspace", str(root), "--forget"])
    assert forgotten.exit_code == 0 and list(root.iterdir()) == []


def test_tty_confirm_resumes_the_same_guarded_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: dict[str, Any]
) -> None:
    root = tmp_path / "interactive"
    monkeypatch.setattr(workspace_guard, "_interactive_confirmation", lambda: True)
    seen: list[Path] = []

    def fake_shell(workspace: Path, **kwargs: object) -> None:
        seen.append(workspace)

    monkeypatch.setattr(cli, "_interactive_shell", fake_shell)
    result = CliRunner().invoke(cli.app, ["--workspace", str(root)], input="y\n")
    assert result.exit_code == 0, result.output
    assert seen == [root] and policy.check_workspace_location(root).confirmed
    assert not (root / "db").exists()


def test_blocked_confirmation_is_not_a_bypass(tmp_path: Path, location: dict[str, Any]) -> None:
    root = tmp_path / "confirmed"
    assert CliRunner().invoke(cli.app, ["accept-location", "--workspace", str(root)]).exit_code == 0
    before = (root / policy.CONFIRMATION_FILE).read_bytes()
    location["kind"] = "KNOWN_SYNC"
    result = CliRunner().invoke(cli.app, ["serve", "--workspace", str(root)])
    assert result.exit_code == 4
    assert (root / policy.CONFIRMATION_FILE).read_bytes() == before
    assert not (root / "db").exists()


def test_tty_rejection_creates_no_confirmation_or_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: dict[str, Any]
) -> None:
    monkeypatch.setattr(workspace_guard, "_interactive_confirmation", lambda: True)
    root = tmp_path / "rejected"
    result = CliRunner().invoke(cli.app, ["--workspace", str(root)], input="n\n")
    assert result.exit_code == 5 and not root.exists()


def test_removable_warns_and_runs_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: dict[str, Any]
) -> None:
    location["kind"] = "REMOVABLE"
    root = tmp_path / "removable"
    seen: list[Path] = []

    def fake_shell(workspace: Path, **kwargs: object) -> None:
        seen.append(workspace)

    monkeypatch.setattr(cli, "_interactive_shell", fake_shell)
    result = CliRunner().invoke(cli.app, ["--workspace", str(root)])
    assert result.exit_code == 0 and seen == [root]
    assert "이동식 드라이브" in result.output and "removable drive" in result.output
    assert not root.exists()


def test_open_bundle_exposes_the_original_location_decision(
    tmp_path: Path, location: dict[str, Any]
) -> None:
    location["kind"] = "REMOVABLE"
    stores = bundle.SqliteStoreFactory().open(tmp_path / "snapshot")
    try:
        initial = stores.workspace_location()
        assert initial is not None and initial.kind == "REMOVABLE"
        calls = location["calls"]
        location["kind"] = "KNOWN_SYNC"
        assert stores.workspace_location() == initial
        assert workspace_location_payload(stores) == location_detail(initial)
        assert location["calls"] == calls
    finally:
        stores.close()


@pytest.mark.parametrize(
    "kind,code",
    [("KNOWN_SYNC", "WORKSPACE_LOCATION_BLOCKED"), ("UNKNOWN", "WORKSPACE_LOCATION_UNCONFIRMED")],
)
async def test_lazy_http_returns_location_503_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: dict[str, Any], kind: str, code: str
) -> None:
    location["kind"] = kind
    root = tmp_path / "http"
    monkeypatch.setenv("THOTH_WORKSPACE", str(root))
    app = http_app.create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.post(
            "/rpc/query",
            json={
                "jsonrpc": "2.0",
                "id": "ready",
                "method": "workspace/ready",
                "params": {"_meta": {"idempotencyKey": "ready"}, "input": {}},
            },
        )
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message_ko"] and detail["message_en"]
    assert not root.exists() and app.state.runtime is None


def test_hosted_factory_bypasses_location_and_lock_and_resets_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: dict[str, Any]
) -> None:
    class StopAtMigration(Exception):
        pass

    def migrate(path: Path) -> None:
        raise StopAtMigration

    def no_lock(*args: object, **kwargs: object) -> None:
        pytest.fail("hosted factory must not acquire the local lock")

    monkeypatch.setattr(bundle, "migrate_sqlite_database", migrate)
    monkeypatch.setattr(bundle, "acquire_workspace_owner", no_lock)
    with pytest.raises(StopAtMigration):
        open_stores(tmp_path / "hosted", deployment_mode=DeploymentMode.HOSTED_REVIEW)
    assert location["calls"] == 0
    assert storage_deployment_mode() is DeploymentMode.LOCAL
    with storage_mode_scope(DeploymentMode.HOSTED_REVIEW):
        assert storage_deployment_mode() is DeploymentMode.HOSTED_REVIEW
    assert storage_deployment_mode() is DeploymentMode.LOCAL


@pytest.mark.parametrize("kind", ["REMOVABLE", "UNKNOWN"])
async def test_ready_uses_the_open_decision_without_reprobing_or_mutable_aliases(
    tmp_path: Path, kind: LocationKind
) -> None:
    snapshot = WorkspaceLocation(
        kind, ("SYNTHETIC",), str(tmp_path), workspace_id(tmp_path), kind == "UNKNOWN"
    )

    class Reporter:
        def workspace_location(self) -> WorkspaceLocation:
            return snapshot

    projection = workspace_location_payload(Reporter())
    assert projection is not None
    handler = WorkspaceSetupHandlers(
        FilesystemWorkspaceSetup(tmp_path),
        model_connected=lambda: False,
        workspace_id=snapshot.workspace_id,
        workspace_location=projection,
    )
    first = await handler.ready({})
    assert first["workspace_location"] == location_detail(snapshot)
    projection["confirmed"] = not snapshot.confirmed
    assert (await handler.ready({}))["workspace_location"] == location_detail(snapshot)
    if kind == "UNKNOWN":
        assert "accept-location" not in str(first["workspace_location"])
        assert replace(snapshot, confirmed=False).confirmed is False
