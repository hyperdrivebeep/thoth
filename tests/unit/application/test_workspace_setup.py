import os
from pathlib import Path
from typing import cast

import pytest

import thoth.application.commands.projects as projects_module
from thoth.adapters.storage.workspace_setup import read_setup, write_setup
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID, PublicWebExecutionStatus
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.protocol.jsonrpc import RpcApplicationError


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _strings(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in cast(list[object], value))
    return cast(list[str], value)


def _merge_policy_payload(
    current: dict[str, object],
    incoming: dict[str, object],
    setup: WorkspaceSetupState,
    *,
    connector_registered: bool,
) -> dict[str, object]:
    merge: object = vars(projects_module).get("_merge_policy_payload")
    assert callable(merge)
    return _record(merge(current, incoming, setup, connector_registered=connector_registered))


def _public_web_execution(
    payload: dict[str, object],
    setup: WorkspaceSetupState,
    connector_registered: bool,
    policy_revision: int,
) -> PublicWebExecutionStatus:
    project: object = vars(projects_module).get("_public_web_execution")
    assert callable(project)
    status: object = project(payload, setup, connector_registered, policy_revision)
    assert isinstance(status, PublicWebExecutionStatus)
    return status


def test_setup_starts_undecided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    assert read_setup(tmp_path).internet_consent == "UNDECIDED"
    assert read_setup(tmp_path).storage_status == "MISSING"


def test_allowed_gets_grant(tmp_path: Path) -> None:
    state = write_setup(
        WorkspaceSetupState(internet_consent="ALLOWED"),
        tmp_path,
    )
    assert state.internet_consent == "ALLOWED"
    assert state.internet_grant_id is not None


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        "[]",
        "{}",
        '{"schema_version":1,"revision":2,"internet_consent":"ALLOWED"}',
        '{"schema_version":1,"revision":2,"internet_consent":"DENIED","internet_grant_id":"grant:stale"}',
        '{"schema_version":99,"revision":2,"internet_consent":"DENIED"}',
    ],
)
def test_existing_damaged_setup_is_not_silent_first_run(tmp_path: Path, raw: str) -> None:
    source = tmp_path / "workspace-setup.json"
    source.write_text(raw, encoding="utf-8")
    assert read_setup(tmp_path).storage_status == "CORRUPT"
    with pytest.raises(RuntimeError):
        write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    assert source.read_text(encoding="utf-8") == raw


def test_unreadable_existing_setup_is_not_silent_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "workspace-setup.json"
    source.write_text("sentinel", encoding="utf-8")
    original = Path.read_text

    def unreadable(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == source:
            raise PermissionError("synthetic denied")
        return original(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert read_setup(tmp_path).storage_status == "UNREADABLE"
    assert source.read_bytes() == b"sentinel"


def test_setup_fsync_failure_preserves_existing_bytes_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = write_setup(WorkspaceSetupState(internet_consent="DENIED"), tmp_path)
    source = tmp_path / "workspace-setup.json"
    before = source.read_bytes()

    def fail_fsync(_fd: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError):
        write_setup(old.model_copy(update={"internet_consent": "ALLOWED"}), tmp_path)
    assert source.read_bytes() == before
    assert list(tmp_path.glob(".workspace-setup.json.*.tmp")) == []


def test_web_on_without_hosts_rejected() -> None:
    try:
        _merge_policy_payload(
            {},
            {"public_web": {"enabled": True, "preferred_hosts": []}},
            WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1"),
            connector_registered=True,
        )
    except RpcApplicationError as exc:
        assert "PUBLIC_WEB_HOSTS_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected reject")


def test_web_on_requires_current_workspace_consent() -> None:
    try:
        _merge_policy_payload(
            {},
            {"public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}},
            WorkspaceSetupState(internet_consent="DENIED"),
            connector_registered=True,
        )
    except RpcApplicationError as exc:
        assert "PUBLIC_WEB_CONSENT_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected reject")


def test_web_policy_binds_grant_and_revocation_blocks_execution() -> None:
    setup = WorkspaceSetupState(
        internet_consent="ALLOWED",
        internet_grant_id="grant:current",
        revision=2,
    )
    payload = _merge_policy_payload(
        {},
        {"public_web": {"enabled": True, "preferred_hosts": ["ARXIV.org"]}},
        setup,
        connector_registered=True,
    )

    assert _record(payload["public_web"]) == {
        "enabled": True,
        "preferred_hosts": ["arxiv.org"],
        "workspace_grant_id": "grant:current",
    }
    assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in _strings(payload["connector_allowlist"])
    assert "ALLOWLISTED_EXTERNAL" in _strings(payload["connector_allowed_egress_classes"])
    assert _public_web_execution(payload, setup, True, 3).state == "READY"

    revoked = WorkspaceSetupState(internet_consent="DENIED", revision=3)
    blocked = _public_web_execution(payload, revoked, True, 3)
    assert blocked.state == "BLOCKED"
    assert "PUBLIC_WEB_CONSENT_REQUIRED" in blocked.reason_codes
    assert "PUBLIC_WEB_GRANT_MISMATCH" in blocked.reason_codes
