from typing import TypedDict, cast

from thoth.application.commands import projects as projects_module
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.protocol.jsonrpc import RpcApplicationError


class _WebView(TypedDict):
    enabled: bool
    preferred_hosts: list[str]
    workspace_grant_id: str | None


class _PolicyView(TypedDict):
    connector_allowlist: list[str]
    connector_allowed_egress_classes: list[str]
    public_web: _WebView


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _strings(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in cast(list[object], value))
    return cast(list[str], value)


def _policy_view(payload: dict[str, object]) -> _PolicyView:
    web = _record(payload["public_web"])
    enabled = web["enabled"]
    grant = web["workspace_grant_id"]
    assert type(enabled) is bool
    assert grant is None or isinstance(grant, str)
    return {
        "connector_allowlist": _strings(payload["connector_allowlist"]),
        "connector_allowed_egress_classes": _strings(
            payload["connector_allowed_egress_classes"]
        ),
        "public_web": {
            "enabled": enabled,
            "preferred_hosts": _strings(web["preferred_hosts"]),
            "workspace_grant_id": grant,
        },
    }


def _merge_policy_payload(
    current: dict[str, object],
    incoming: dict[str, object],
    workspace_setup: WorkspaceSetupState | None = None,
    *,
    connector_registered: bool = False,
) -> dict[str, object]:
    merge: object = vars(projects_module).get("_merge_policy_payload")
    assert callable(merge)
    return _record(
        merge(
            current,
            incoming,
            workspace_setup,
            connector_registered=connector_registered,
        )
    )


def _base() -> dict[str, object]:
    return {
        "connector_allowlist": ["local-file-upload", "read-only-git-snapshot"],
        "connector_allowed_egress_classes": ["NONE"],
        "connector_default": "DENY",
        "public_web": {"enabled": False, "preferred_hosts": [], "workspace_grant_id": None},
    }


def test_on_arxiv_adds_managed_id_and_external_egress() -> None:
    setup = WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1")
    merged = _merge_policy_payload(
        _base(),
        {"public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}},
        setup,
        connector_registered=True,
    )
    view = _policy_view(merged)
    assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in view["connector_allowlist"]
    assert "ALLOWLISTED_EXTERNAL" in view["connector_allowed_egress_classes"]
    assert "NONE" in view["connector_allowed_egress_classes"]
    assert view["public_web"]["preferred_hosts"] == ["arxiv.org"]
    assert view["public_web"]["workspace_grant_id"] == "grant:1"


def test_off_removes_managed_id_and_keeps_local() -> None:
    setup = WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1")
    on = _merge_policy_payload(
        _base(),
        {"public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}},
        setup,
        connector_registered=True,
    )
    off = _merge_policy_payload(
        on,
        {"public_web": {"enabled": False, "preferred_hosts": ["arxiv.org"]}},
        setup,
        connector_registered=True,
    )
    view = _policy_view(off)
    assert PROJECT_PUBLIC_WEB_CONNECTOR_ID not in view["connector_allowlist"]
    assert view["connector_allowlist"] == ["local-file-upload", "read-only-git-snapshot"]
    assert "ALLOWLISTED_EXTERNAL" not in view["connector_allowed_egress_classes"]


def test_off_preserves_preexisting_connector_and_egress_permissions() -> None:
    setup = WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1")
    current = {
        **_base(),
        "connector_allowlist": [
            "local-file-upload",
            "read-only-git-snapshot",
            PROJECT_PUBLIC_WEB_CONNECTOR_ID,
        ],
        "connector_allowed_egress_classes": ["NONE", "ALLOWLISTED_EXTERNAL"],
    }
    on = _merge_policy_payload(
        current,
        {"public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}},
        setup,
        connector_registered=True,
    )
    off = _merge_policy_payload(
        on,
        {"public_web": {"enabled": False, "preferred_hosts": ["arxiv.org"]}},
        setup,
        connector_registered=True,
    )
    view = _policy_view(off)
    assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in view["connector_allowlist"]
    assert "ALLOWLISTED_EXTERNAL" in view["connector_allowed_egress_classes"]


def test_legacy_public_web_only_payload_is_repaired_on_resave() -> None:
    setup = WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1")
    current = {
        **_base(),
        "public_web": {
            "enabled": True,
            "preferred_hosts": ["arxiv.org"],
            "workspace_grant_id": "grant:1",
        },
    }
    merged = _merge_policy_payload(
        current,
        {
            "public_web": {
                "enabled": True,
                "preferred_hosts": ["arxiv.org"],
                "workspace_grant_id": "grant:1",
            }
        },
        setup,
        connector_registered=True,
    )
    view = _policy_view(merged)
    assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in view["connector_allowlist"]
    assert "ALLOWLISTED_EXTERNAL" in view["connector_allowed_egress_classes"]


def test_empty_hosts_on_is_rejected() -> None:
    try:
        _merge_policy_payload(
            _base(),
            {"public_web": {"enabled": True, "preferred_hosts": []}},
            WorkspaceSetupState(internet_consent="ALLOWED", internet_grant_id="grant:1"),
            connector_registered=True,
        )
    except RpcApplicationError as exc:
        assert "PUBLIC_WEB_HOSTS_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected reject")


def test_on_without_workspace_consent_is_rejected() -> None:
    try:
        _merge_policy_payload(
            _base(),
            {"public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}},
            WorkspaceSetupState(),
            connector_registered=True,
        )
    except RpcApplicationError as exc:
        assert "PUBLIC_WEB_CONSENT_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected reject")
