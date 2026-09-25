from pathlib import Path
from typing import cast

import pytest
from tests.integration.scoped_runtime import create_runtime

from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {"id": key, "method": method, "params": {"_meta": {"idempotencyKey": key}, "input": value}}
    )


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert type(value) is int
    return value


def _rpc_value(response: JsonRpcResponse) -> dict[str, object]:
    assert response.result is not None
    return _record(response.result["value"])


async def test_client_eligible_does_not_store_after_cutoff_or_unknown(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "later.html").write_text(
        '<html><body><article itemscope itemtype="https://schema.org/Article">'
        '<meta property="article:published_time" content="2026-12-01T00:00:00Z" />'
        "<p>later body</p></article></body></html>",
        encoding="utf-8",
    )
    (inbox / "undated.md").write_text("# Note\n\nNo document date.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "create",
                {"project_id": "project:time", "name": "Time", "cutoff_at": "2026-01-01T00:00:00Z"},
            )
        )
        later = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "later",
                {
                    "project_id": "project:time",
                    "relative_path": "later.html",
                    "media_type": "text/html",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        undated = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "undated",
                {
                    "project_id": "project:time",
                    "relative_path": "undated.md",
                    "media_type": "text/markdown",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        listed = await runtime.bus.dispatch(
            _rpc("project/source/list", "list", {"project_id": "project:time"})
        )
    finally:
        runtime.close()
    assert later.error is None and undated.error is None and listed.result is not None
    later_value = _rpc_value(later)
    undated_value = _rpc_value(undated)
    assert isinstance(later_value, dict) and isinstance(undated_value, dict)
    assert _text(_record(later_value["source_time"])["cutoff_state"]) == "AFTER_CUTOFF"
    assert _text(_record(undated_value["source_time"])["cutoff_state"]) == "UNKNOWN_TIME"
    listed_value = _rpc_value(listed)
    assert isinstance(listed_value, dict)
    artifacts = _list(listed_value["artifacts"])
    assert isinstance(artifacts, list)
    states = {_text(_record(item)["cutoff_state"]) for item in artifacts}
    assert states == {"AFTER_CUTOFF", "UNKNOWN_TIME"}


async def test_unknown_source_can_be_confirmed_then_cited_state_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "undated.md").write_text("# Note\n\nNo document date.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "create",
                {"project_id": "project:time", "name": "Time", "cutoff_at": "2026-01-01T00:00:00Z"},
            )
        )
        connected = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "undated",
                {
                    "project_id": "project:time",
                    "relative_path": "undated.md",
                    "media_type": "text/markdown",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        listed = await runtime.bus.dispatch(
            _rpc("project/source/list", "list", {"project_id": "project:time"})
        )
        assert connected.result is not None and listed.result is not None
        connected_value = _rpc_value(connected)
        listed_value = _rpc_value(listed)
        assert isinstance(connected_value, dict) and isinstance(listed_value, dict)
        source_time = _record(connected_value["source_time"])
        times = _list(listed_value["source_times"])
        cutoff = _record(listed_value["cutoff_basis"])
        assert isinstance(source_time, dict)
        assert isinstance(times, list)
        assert isinstance(cutoff, dict)
        current = _record(times[0])
        confirmed = await runtime.bus.dispatch(
            _rpc(
                "project/source/time/confirm",
                "confirm",
                {
                    "project_id": "project:time",
                    "artifact_id": _text(current["artifact_id"]),
                    "source_version_id": _text(current["source_version_id"]),
                    "byte_sha256": _text(current["byte_sha256"]),
                    "expected_project_revision": _integer(cutoff["project_revision"]),
                    "expected_cutoff_at": _text(cutoff["cutoff_at"]),
                    "expected_assessment_revision": _integer(current["revision"]),
                    "expected_metadata_digest": _text(current["metadata_digest"]),
                    "assertion": "ON_OR_BEFORE_CUTOFF",
                },
            )
        )
    finally:
        runtime.close()
    assert confirmed.error is None and confirmed.result is not None
    confirmed_value = _rpc_value(confirmed)
    assert isinstance(confirmed_value, dict)
    assert _text(_record(confirmed_value["source_time"])["cutoff_state"]) == "ELIGIBLE"


async def test_source_time_confirmation_rolls_back_when_audit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "undated.md").write_text("# Note\n\nNo document date.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "create",
                {"project_id": "project:time", "name": "Time", "cutoff_at": "2026-01-01T00:00:00Z"},
            )
        )
        await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "undated",
                {
                    "project_id": "project:time",
                    "relative_path": "undated.md",
                    "media_type": "text/markdown",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        listed = await runtime.bus.dispatch(
            _rpc("project/source/list", "list-before", {"project_id": "project:time"})
        )
        assert listed.result is not None
        listed_value = _rpc_value(listed)
        assert isinstance(listed_value, dict)
        current = _record(_list(listed_value["source_times"])[0])
        cutoff = _record(listed_value["cutoff_basis"])

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected source-time audit failure")

        monkeypatch.setattr(EvidenceGraphService, "audit", fail_audit)
        failed = await runtime.bus.dispatch(
            _rpc(
                "project/source/time/confirm",
                "confirm-fails",
                {
                    "project_id": "project:time",
                    "artifact_id": _text(current["artifact_id"]),
                    "source_version_id": _text(current["source_version_id"]),
                    "byte_sha256": _text(current["byte_sha256"]),
                    "expected_project_revision": _integer(cutoff["project_revision"]),
                    "expected_cutoff_at": _text(cutoff["cutoff_at"]),
                    "expected_assessment_revision": _integer(current["revision"]),
                    "expected_metadata_digest": _text(current["metadata_digest"]),
                    "assertion": "ON_OR_BEFORE_CUTOFF",
                },
            )
        )
        assert failed.error is not None
        after = await runtime.bus.dispatch(
            _rpc("project/source/list", "list-after", {"project_id": "project:time"})
        )
    finally:
        runtime.close()
    assert after.result is not None
    after_value = _rpc_value(after)
    assert isinstance(after_value, dict)
    assert _text(_record(_list(after_value["artifacts"])[0])["cutoff_state"]) == "UNKNOWN_TIME"
    assert _text(_record(_list(after_value["source_times"])[0])["cutoff_state"]) == "UNKNOWN_TIME"
