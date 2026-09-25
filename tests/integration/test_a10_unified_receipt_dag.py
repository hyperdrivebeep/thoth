from __future__ import annotations

from pathlib import Path
from typing import cast

import orjson
import pytest
from alembic import command
from alembic.config import Config
from pydantic import JsonValue
from sqlalchemy import create_engine, func, inspect, select, update
from tests.integration.test_a04_r2_closed_loop import prepare_a04, request, value

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.receipt_dag import SqliteReceiptDagStore
from thoth.adapters.storage.schema import receipt_dag_bundles, receipt_dag_edges
from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.domain.receipt_dag import ReceiptNodeKind
from thoth.ports.control_record import ControlRecordStorePort


class EmptyControls:
    def list(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()


@pytest.mark.asyncio
async def test_normal_r2_cycle_materializes_typed_unified_receipt_dag(
    tmp_path: Path,
) -> None:
    runtime, _sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=True,
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a10-normal-cycle",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/audit/read",
                    "a10-receipt-audit",
                    {"project_id": project_id},
                )
            )
        )
    finally:
        runtime.close()

    dag = cast(dict[str, JsonValue], audit["dag"])
    nodes = cast(list[dict[str, JsonValue]], dag["nodes"])
    kinds = {str(node["kind"]) for node in nodes}
    assert {
        "CONNECTOR",
        "SANDBOX",
        "EVIDENCE",
        "MODEL",
        "ACTION",
        "EXECUTION",
        "OUTCOME",
        "REVISION",
        "MEMORY",
    }.issubset(kinds)
    assert cast(list[object], dag["edges"])
    axes = cast(dict[str, JsonValue], dag["axes"])
    assert axes["semantic_truth"] == "NOT_CERTIFIED"
    assert axes["authorization"] == "SEPARATE"
    assert axes["integrity"] == "VALID"
    assert axes["provenance"] in {"COMPLETE", "PARTIAL"}


@pytest.mark.asyncio
async def test_typed_receipt_bundle_detects_corruption_and_missing_parent(
    tmp_path: Path,
) -> None:
    runtime, _sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=True,
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a10-corruption-cycle",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        with runtime.ledger.engine.connect() as connection:
            bundles_before = int(
                connection.execute(
                    select(func.count()).select_from(receipt_dag_bundles)
                ).scalar_one()
            )
        unknown = await runtime.bus.dispatch(
            request(
                "receipt/bundle/create",
                "a10-bundle-unknown-selector",
                {
                    "project_id": project_id,
                    "receipt_ids": ["receipt:does-not-exist"],
                },
            )
        )
        assert unknown.error is not None
        with runtime.ledger.engine.connect() as connection:
            bundles_after = int(
                connection.execute(
                    select(func.count()).select_from(receipt_dag_bundles)
                ).scalar_one()
            )
        assert bundles_after == bundles_before
        created = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/bundle/create",
                    "a10-bundle-create",
                    {"project_id": project_id, "receipt_ids": []},
                )
            )
        )
        bundle = cast(dict[str, JsonValue], created["bundle"])
        bundle_id = str(bundle["bundle_id"])
        valid = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/bundle/verify",
                    "a10-bundle-valid",
                    {"project_id": project_id, "bundle_id": bundle_id},
                )
            )
        )
        assert valid["integrity_state"] == "VALID"

        corrupted = dict(cast(dict[str, JsonValue], bundle["payload"]))
        corrupted["node_ids"] = ["receipt-node:missing"]
        with runtime.ledger.engine.begin() as connection:
            connection.execute(
                update(receipt_dag_bundles)
                .where(receipt_dag_bundles.c.bundle_id == bundle_id)
                .values(content_json=orjson.dumps(corrupted).decode())
            )
            edge_row = connection.execute(
                select(
                    receipt_dag_edges.c.edge_id,
                    receipt_dag_edges.c.content_json,
                ).limit(1)
            ).one()
            edge_id = str(edge_row.edge_id)
            edge_content = cast(dict[str, object], orjson.loads(str(edge_row.content_json)))
            edge_content["parent_node_id"] = "receipt-node:missing"
            connection.execute(
                update(receipt_dag_edges)
                .where(receipt_dag_edges.c.edge_id == edge_id)
                .values(
                    parent_node_id="receipt-node:missing",
                    content_json=orjson.dumps(edge_content).decode(),
                )
            )
        invalid = await runtime.bus.dispatch(
            request(
                "receipt/bundle/verify",
                "a10-bundle-invalid",
                {"project_id": project_id, "bundle_id": bundle_id},
            )
        )
        assert invalid.error is not None
        assert invalid.error.data == {"reason_code": "RESOURCE_LINEAGE_UNKNOWN"}
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/audit/read",
                    "a10-missing-parent-audit",
                    {"project_id": project_id},
                )
            )
        )
    finally:
        runtime.close()

    dag = cast(dict[str, JsonValue], audit["dag"])
    assert cast(dict[str, JsonValue], dag["axes"])["integrity"] == "INVALID"


def test_receipt_dag_ids_are_project_scoped_and_same_project_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thoth.apps.runtime import create_runtime

    runtime = create_runtime(tmp_path / "project-keys")
    store = SqliteReceiptDagStore(runtime.ledger.engine)
    service = ReceiptDagService(
        store=store,
        ledger=runtime.ledger,
        controls=cast(ControlRecordStorePort, EmptyControls()),
        clock=SystemClock(),
    )
    values = {
        ReceiptNodeKind.MODEL: ("model:shared",),
        ReceiptNodeKind.ACTION: ("action:shared",),
    }
    try:
        first = service.record_cycle("project:a10:a", values)
        second = service.record_cycle("project:a10:b", values)
        replay = service.record_cycle("project:a10:a", values)
        first_nodes = store.list_nodes("project:a10:a")
        second_nodes = store.list_nodes("project:a10:b")
        first_edges = store.list_edges("project:a10:a")
        second_edges = store.list_edges("project:a10:b")
        node_pk = inspect(runtime.ledger.engine).get_pk_constraint("receipt_dag_nodes")
        edge_pk = inspect(runtime.ledger.engine).get_pk_constraint("receipt_dag_edges")
        with pytest.raises(ValueError, match="another project"):
            store.put_manifest(second.model_copy(update={"manifest_id": first.manifest_id}))
        first_manifest = store.read_manifest("project:a10:a")
        second_manifest = store.read_manifest("project:a10:b")
    finally:
        runtime.close()

    assert first.axes.integrity == "VALID"
    assert second.axes.integrity == "VALID"
    assert replay.axes.integrity == "VALID"
    assert len(first_nodes) == len(second_nodes) == 2
    assert len(first_edges) == len(second_edges) == 1
    assert {node.project_id for node in (*first_nodes, *second_nodes)} == {
        "project:a10:a",
        "project:a10:b",
    }
    assert set(node_pk["constrained_columns"]) == {"project_id", "node_id"}
    assert set(edge_pk["constrained_columns"]) == {"project_id", "edge_id"}
    assert first_manifest is not None and first_manifest.project_id == "project:a10:a"
    assert second_manifest is not None and second_manifest.project_id == "project:a10:b"

    database = tmp_path / "project-keys" / "db" / "thoth.sqlite3"
    monkeypatch.setenv("THOTH_ALEMBIC_URL", "sqlite+pysqlite:///" + database.as_posix())
    with pytest.raises(RuntimeError, match="cross-project duplicate identifiers"):
        command.downgrade(Config("alembic.ini"), "d5e47f9013ab")
    engine = create_engine("sqlite+pysqlite:///" + database.as_posix())
    try:
        assert set(
            inspect(engine).get_pk_constraint("receipt_dag_nodes")["constrained_columns"]
        ) == {
            "project_id",
            "node_id",
        }
        with engine.connect() as connection:
            assert (
                int(
                    connection.execute(
                        select(func.count()).select_from(receipt_dag_edges)
                    ).scalar_one()
                )
                == 2
            )
    finally:
        engine.dispose()
