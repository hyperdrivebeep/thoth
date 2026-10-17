"""Freeze public account projections with synthetic inputs and fail-fast I/O guards."""

from __future__ import annotations

import builtins
import copy
import io
import json
import os
import socket
import subprocess
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from thoth.adapters.models import local_credentials

MARKER = "UNUSED_SYNTHETIC_FIELD"


@dataclass(frozen=True)
class ProjectionCase:
    name: str
    codex: dict[str, object] = field(default_factory=dict)
    xai: dict[str, object] = field(default_factory=dict)
    pkce: dict[str, object] = field(default_factory=dict)
    code: dict[str, object] = field(default_factory=dict)
    registered_providers: tuple[str, ...] = ()


def status(state: str, *, connected: bool = False, eligible: bool = False) -> dict[str, object]:
    return {
        "connection_state": state,
        "reason_code": state,
        "connected": connected,
        "execution_eligible": eligible,
    }


CASES = (
    ProjectionCase("P0_empty"),
    ProjectionCase(
        "P1_codex_connected_ineligible", codex=status("CATALOG_UNAVAILABLE", connected=True)
    ),
    ProjectionCase(
        "P2_openai_key_and_oauth",
        codex=status("EXECUTION_UNVERIFIED", connected=True, eligible=True),
        registered_providers=("openai",),
    ),
    ProjectionCase(
        "P3_xai_key_and_oauth",
        xai=status("EXECUTION_UNVERIFIED", connected=True, eligible=True),
        registered_providers=("xai",),
    ),
    ProjectionCase(
        "P4_code_tool_unavailable",
        code={**status("UNAVAILABLE"), "reason_code": "CLAUDE_CODE_NOT_INSTALLED"},
    ),
    ProjectionCase("P5_code_signed_out", code=status("LOGIN_REQUIRED")),
    ProjectionCase(
        "P6_code_eligible", code=status("EXECUTION_UNVERIFIED", connected=True, eligible=True)
    ),
    ProjectionCase(
        "P7_pkce_enabled_signed_out",
        code=status("LOGIN_REQUIRED"),
        pkce={"capabilities": {"start": True}, "auth_state": "DISCONNECTED"},
    ),
    ProjectionCase(
        "P8_claude_three_methods",
        code=status("EXECUTION_UNVERIFIED", connected=True, eligible=True),
        pkce={
            **status("EXECUTION_UNVERIFIED", connected=True, eligible=True),
            "auth_state": "CONNECTED",
            "capabilities": {"start": True},
        },
        registered_providers=("anthropic",),
    ),
    ProjectionCase(
        "P9_explicit_none",
        codex={"reason_code": None, "connection_state": None},
        xai={"reason_code": None, "connection_state": None},
        code={**status("ERROR"), "reason_code": None},
        pkce={"capabilities": {"start": True}, "reason_code": None, "auth_state": None},
    ),
)


def observed_projection(
    case: ProjectionCase, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    codex, xai, pkce, code = copy.deepcopy((case.codex, case.xai, case.pkce, case.code))
    # Do not make an originally empty mapping truthy: bool(code) is part of the contract.
    for mapping in (codex, xai, pkce, code):
        if mapping:
            mapping["unused_projection_marker"] = MARKER
    public_credentials = tuple(
        {
            "provider": provider,
            "model": "synthetic-model",
            "kind": "api_key",
            "base_url": "https://example.invalid/v1",
        }
        for provider in case.registered_providers
    )
    before = copy.deepcopy((codex, xai, pkce, code, public_credentials))
    reads: list[Path | None] = []
    attempted_io: list[str] = []

    def listed(root: Path | None = None) -> tuple[dict[str, str], ...]:
        reads.append(root)
        return public_credentials

    def deny(label: str):
        def blocked(*_args: object, **_kwargs: object) -> None:
            attempted_io.append(label)
            raise AssertionError("UNEXPECTED_PROJECTION_IO:" + label)

        return blocked

    with monkeypatch.context() as guard:
        guard.setattr(local_credentials, "available_credentials", listed)
        for owner, names in (
            (builtins, ("open",)),
            (io, ("open",)),
            (
                os,
                (
                    "open",
                    "stat",
                    "lstat",
                    "listdir",
                    "scandir",
                    "mkdir",
                    "chmod",
                    "rename",
                    "replace",
                    "remove",
                    "unlink",
                ),
            ),
            (subprocess, ("Popen", "run")),
            (socket, ("socket", "create_connection", "getaddrinfo")),
            (httpx, ("Client", "AsyncClient")),
            (webbrowser, ("open",)),
            (threading.Thread, ("start",)),
        ):
            for name in names:
                guard.setattr(owner, name, deny(name))
        rows = local_credentials.account_connections(codex, workspace, xai, pkce, code)
    assert reads == [workspace]
    assert attempted_io == []
    assert (codex, xai, pkce, code, public_credentials) == before
    assert [row["provider"] for row in rows] == ["openai", "anthropic", "xai"]
    assert all(row["execution_verified"] is False for row in rows)
    assert all(row["remote_auth_verified"] is None for row in rows)
    wire = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    assert MARKER not in wire and "unused_projection_marker" not in wire
    return wire


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_original_public_auth_projection_is_preserved(
    case: ProjectionCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "credential_auth_projection_v1.json"
    expected = json.loads(fixture.read_text(encoding="utf-8"))
    assert expected["schema_version"] == 1
    assert observed_projection(case, tmp_path, monkeypatch) == expected["cases"][case.name]
