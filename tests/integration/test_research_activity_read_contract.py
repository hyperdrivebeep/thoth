"""Freeze activity read authorization, cursor rejection and legacy-list identity."""

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services import conversation_inputs as conversation_module
from thoth.domain.research_request import InputDelivery
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


@pytest.mark.parametrize("cursor", (0, -1, True, 1.5, "2", []))
async def test_invalid_cursor_never_calls_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor: JsonValue
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        host = research_host(runtime)
        calls: list[str] = []

        async def forbidden(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
            calls.append("legacy")
            pytest.fail("invalid cursor reached legacy reader")

        monkeypatch.setattr(host.legacy, "activity_list", forbidden)
        with pytest.raises(RpcApplicationError) as raised:
            await host.activity_list(
                {"project_id": "p", "thread_id": "fixture", "conversation_before_epoch": cursor}
            )
        assert raised.value.code == RpcErrorCode.INVALID_PARAMS
        assert raised.value.message == "invalid conversation cursor" and calls == []
    finally:
        runtime.close()


async def test_legacy_failure_precedes_all_revision_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        host = research_host(runtime)
        calls: list[str] = []

        async def failure(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
            calls.append("legacy")
            raise RuntimeError("legacy read failed")

        def forbidden(*_args: object) -> tuple[SemanticRevision, ...]:
            pytest.fail("legacy failure reached ledger")

        monkeypatch.setattr(host.legacy, "activity_list", failure)
        monkeypatch.setattr(host.records.ledger, "read_revisions", forbidden)
        with pytest.raises(RuntimeError, match="legacy read failed"):
            await host.activity_list({"project_id": "p", "thread_id": "fixture"})
        assert calls == ["legacy"]
    finally:
        runtime.close()


@pytest.mark.parametrize("cursor", (None, 2))
async def test_activity_order_acl_skips_and_legacy_list_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor: int | None
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "question",
                    {"project_id": "p", "problem": "Fixture question", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        thread = str(accepted["thread_id"])
        host = research_host(runtime)
        stored = host.records.ledger.read_revisions("p", "THREAD", "request:" + thread)[0]
        denied = stored.model_copy(
            update={"revision_id": "denied", "revision_digest": "d" * 64, "snapshot_id": "denied"}
        )
        missing = stored.model_copy(
            update={"revision_id": "missing", "revision_digest": "e" * 64, "snapshot_id": "missing"}
        )
        events: list[str] = []
        legacy_list: list[JsonValue] = [
            {"activity_id": "late", "created_at": "9999-01-01T00:00:00+00:00"},
            "legacy marker",
            {"activity_id": "no-date"},
            {"activity_id": "early", "created_at": "0001-01-01T00:00:00+00:00"},
            {"activity_id": "offset-a", "created_at": "2026-01-01T01:00:00+01:00"},
            {"activity_id": "offset-z", "created_at": "2026-01-01T00:30:00+00:00"},
        ]
        base: dict[str, JsonValue] = {"activities": legacy_list, "discarded": "legacy-only"}
        original_snapshot = host.records.ledger.read_snapshot
        original_journal = host.records.journal_read
        parameters: dict[str, JsonValue] = {
            "project_id": "p",
            "thread_id": thread,
            "conversation_before_epoch": cursor,
        }

        async def legacy(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
            assert params == {"project_id": "p", "thread_id": thread}
            assert parameters["conversation_before_epoch"] == cursor
            events.append("legacy.enter")
            await asyncio.sleep(0)
            events.append("legacy.exit")
            return base

        def revisions(project: str, entity: str, identifier: str) -> tuple[SemanticRevision, ...]:
            assert (project, entity, identifier) == ("p", "THREAD", "request:" + thread)
            events.append("revisions")
            return denied, missing, stored

        def may_read(project: str, digest: str) -> bool:
            assert project == "p"
            label = (
                "denied"
                if digest == denied.revision_digest
                else "missing"
                if digest == missing.revision_digest
                else "stored"
            )
            events.append("acl:" + label)
            return label != "denied"

        def snapshot(identifier: str) -> EntitySnapshot | None:
            assert identifier != "denied"
            events.append("snapshot:" + ("missing" if identifier == "missing" else "stored"))
            return None if identifier == "missing" else original_snapshot(identifier)

        def journal[T: BaseModel](project: str, identifier: str, codec: type[T]) -> T | None:
            assert codec is InputDelivery
            events.append("delivery")
            return None

        def conversation(
            records: object, access: object, project: str, identifier: str, before: int | None
        ) -> dict[str, JsonValue]:
            assert records is host.records and access is host.access
            assert (project, identifier, before) == ("p", thread, cursor)
            events.append("conversation")
            return {"turns": []}

        def no_transaction() -> None:
            pytest.fail("activity reader added a transaction")

        del original_journal
        with monkeypatch.context() as patch:
            patch.setattr(host.legacy, "activity_list", legacy)
            patch.setattr(host.records.ledger, "read_revisions", revisions)
            patch.setattr(host.records.ledger, "read_snapshot", snapshot)
            patch.setattr(host.records.ledger, "transaction", no_transaction)
            patch.setattr(host.access, "may_read_revision", may_read)
            patch.setattr(host.records, "journal_read", journal)
            patch.setattr(conversation_module, "conversation_inputs", conversation)
            result = await host.activity_list(parameters)
        assert base["activities"] is legacy_list and len(legacy_list) == 7
        assert result is not base and result["activities"] is not legacy_list
        assert set(result) == {"activities", "conversation"}
        assert events == [
            "legacy.enter",
            "legacy.exit",
            "revisions",
            "acl:denied",
            "acl:missing",
            "snapshot:missing",
            "acl:stored",
            "snapshot:stored",
            "delivery",
            "conversation",
        ]
        ordered = result["activities"]
        assert isinstance(ordered, list)
        assert ordered[0] == "legacy marker" and ordered[1] == {"activity_id": "no-date"}
        assert [item["activity_id"] for item in ordered[2:] if isinstance(item, dict)] == [
            "early",
            "offset-z",
            "offset-a",
            stored.revision_id,
            "late",
        ]
        added = legacy_list[-1]
        assert isinstance(added, dict) and added["event_type"] == "research/inputAccepted"
        payload = added["payload"]
        assert isinstance(payload, dict) and payload["input_state"] is None
    finally:
        runtime.close()
