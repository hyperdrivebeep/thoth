"""Late faults preserve auxiliary and owner records in one public checkpoint."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.research_measurement_helpers import connect_local_measurement_contract
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_portfolio_atomicity import portfolio_input
from tests.integration.test_a03_storage_atomicity import create_hypothesis, setup

from thoth.adapters.storage import transaction as transaction_module
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.objects import ContentAddressedObjectStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import JsonRpcRequest

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step74-20261014"
_H = "thoth.application.services.hypothesis_service"
_R = "thoth.application.services.revision_service"
_S = "thoth.adapters.storage.sqlite"
_T = "thoth.adapters.storage.hypothesis"
_I = "thoth.adapters.storage.investigation"
_BASE = [
    _R + ".commit",
    _S + ".insert_snapshot",
    _S + ".insert_revision",
    _S + ".set_head",
    _S + ".insert_receipt",
]
_KINDS = ("assumption", "prediction", "relation_add", "relation_remove", "revalidate", "counter")


def _save(name: str, data: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / name).write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def _rows(state: dict[str, tuple[str, ...]], table: str) -> list[dict[str, Any]]:
    return [json.loads(r) for r in state[table]]


def _is_portfolio(kind: str) -> bool:
    return kind in {"relation_add", "relation_remove", "revalidate"}


def _relation_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": ctx["project"],
        "portfolio_id": ctx["portfolio"]["portfolio_id"],
        "source_hypothesis_id": ctx["hypothesis_ids"][0],
        "target_hypothesis_id": ctx["hypothesis_ids"][1],
        "relation_type": "ALTERNATIVE_TO",
        "evidence_refs": ctx["spans"],
        "expected_portfolio_revision": ctx["portfolio"]["revision_digest"],
    }


def _assumption_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": ctx["project"],
        "hypothesis_id": ctx["current"]["hypothesis_id"],
        "expected_revision_digest": ctx["current"]["revision_digest"],
        "statement": "Clock calibration still needs independent confirmation",
        "role": "MEASUREMENT",
        "evidence_refs": ctx["spans"],
        "validation_route": None,
    }


async def _prepare(tmp_path: Path, kind: str) -> tuple[Any, dict[str, Any]]:
    runtime, ctx = await setup(tmp_path)
    try:
        if _is_portfolio(kind):
            payload = await portfolio_input(runtime, ctx)
            ctx["hypothesis_ids"] = payload["hypothesis_ids"]
            ctx["portfolio"] = value(
                await runtime.bus.dispatch(
                    request("hypothesis/portfolio/compose", "setup-portfolio", payload)
                )
            )["portfolio"]
            if kind == "relation_remove":
                added = value(
                    await runtime.bus.dispatch(
                        request("hypothesis/relation/add", "setup-relation", _relation_payload(ctx))
                    )
                )
                ctx.update(portfolio=added["portfolio"], relation=added["relation"])
        else:
            ctx["current"] = await create_hypothesis(runtime, ctx)
        if kind == "prediction":
            measurement = await connect_local_measurement_contract(
                runtime, ctx["project"], tmp_path
            )
            artifacts = _rows(snapshot(runtime.ledger.engine), "artifacts")
            ctx["contract_digest"] = next(
                r["byte_sha256"] for r in artifacts if r["artifact_id"] == measurement
            )
            ctx["method"] = "hypothesis/prediction/bind"
            ctx["payload"] = {
                "project_id": ctx["project"],
                "hypothesis_id": ctx["current"]["hypothesis_id"],
                "hypothesis_revision_digest": ctx["current"]["revision_digest"],
                "knowledge_cutoff": "2026-09-05T00:00:00Z",
                "prespecification_state": "A_PRIORI",
                "conditions": {"dataset_version": "trial-v1"},
                "measurement_contract_ref": measurement,
                "assumption_refs": [],
                "expected_outcome": {
                    "type": "RANGE",
                    "measure": "latency",
                    "unit": "ms",
                    "lower": 9,
                    "upper": 13,
                },
                "discrimination_map": {},
            }
            ctx["reads"] = tuple(
                sorted(
                    {
                        f"revision:{ctx['current']['revision_digest']}",
                        *(r["artifact_id"] for r in artifacts),
                    }
                )
            )
        elif kind == "assumption":
            ctx.update(
                method="hypothesis/assumption/add",
                payload=_assumption_payload(ctx),
                reads=tuple(
                    sorted({f"revision:{ctx['current']['revision_digest']}", *ctx["spans"]})
                ),
            )
        elif kind == "counter":
            object_digest = runtime.ledger.read_heads(ctx["project"])[
                "DECISION_OBJECT:" + ctx["object"]
            ]
            ctx.update(
                method="hypothesis/counterevidence/request",
                payload={
                    "project_id": ctx["project"],
                    "hypothesis_id": ctx["current"]["hypothesis_id"],
                    "source_scope": ["independent sources"],
                },
                reads=tuple(
                    sorted(
                        {
                            f"revision:{ctx['current']['revision_digest']}",
                            f"revision:{object_digest}",
                        }
                    )
                ),
            )
        elif kind == "revalidate":
            ctx.update(
                method="hypothesis/portfolio/revalidate",
                payload={
                    "project_id": ctx["project"],
                    "portfolio_id": ctx["portfolio"]["portfolio_id"],
                    "trigger_reason": "74-explicit-revalidation",
                },
                reads=(f"revision:{ctx['portfolio']['revision_digest']}",),
            )
        else:
            method = (
                "hypothesis/relation/add"
                if kind == "relation_add"
                else "hypothesis/relation/remove"
            )
            payload = (
                _relation_payload(ctx)
                if kind == "relation_add"
                else {
                    "project_id": ctx["project"],
                    "relation_id": ctx["relation"]["relation_id"],
                    "reason": "74-explicit-removal",
                    "evidence_refs": ctx["spans"],
                    "expected_portfolio_revision": ctx["portfolio"]["revision_digest"],
                }
            )
            heads = runtime.ledger.read_heads(ctx["project"])
            ctx.update(
                method=method,
                payload=payload,
                reads=tuple(
                    sorted(
                        {
                            f"revision:{ctx['portfolio']['revision_digest']}",
                            *(
                                f"revision:{heads['HYPOTHESIS:' + h]}"
                                for h in ctx["hypothesis_ids"]
                            ),
                            *ctx["spans"],
                        }
                    )
                ),
            )
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


@contextmanager
def _observe(command: JsonRpcRequest, data: dict[str, Any]) -> Generator[None]:
    revisions: dict[str, str] = {}
    keep = {
        *_BASE,
        *(_H + "." + n for n in ("_persist_hypothesis", "_persist_portfolio", "_commit_record")),
        *(
            _T + "." + n
            for n in (
                "add_hypothesis",
                "add_portfolio",
                "add_assumption",
                "add_prediction",
                "add_relation",
                "append_audit",
            )
        ),
        _I + ".create",
        _I + ".append_audit",
    }

    def profile(frame: FrameType, event: str, _arg: Any) -> None:
        op = current_operation.get()
        if (
            event != "call"
            or op is None
            or (op.method, op.idempotency_key, op.project_id)
            != (
                command.method,
                command.params.meta.idempotency_key,
                command.params.input["project_id"],
            )
        ):
            return
        symbol = str(frame.f_globals.get("__name__")) + "." + frame.f_code.co_name
        if symbol not in keep:
            return
        args = frame.f_locals
        entities: list[str] = []
        if symbol.startswith(_H + "."):
            record = args["record"]
            if symbol.endswith("_commit_record"):
                entities = [args["entity_id"]]
                revisions[args["revision_id"]] = args["entity_id"]
            else:
                entities = [
                    record.portfolio_id
                    if symbol.endswith("_persist_portfolio")
                    else record.hypothesis_id
                ]
        elif symbol == _R + ".commit":
            for staged in args["changeset"].staged_revisions:
                if staged.revision.entity_type.value == "HYPOTHESIS":
                    entities.append(staged.revision.entity_id)
                    revisions[staged.revision.revision_id] = staged.revision.entity_id
        elif symbol in {_S + ".insert_snapshot", _S + ".insert_revision"}:
            record = args["snapshot" if symbol.endswith("insert_snapshot") else "revision"]
            if record.entity_type.value == "HYPOTHESIS":
                entities = [record.entity_id]
        elif symbol == _S + ".set_head":
            if args["aggregate_key"].startswith("HYPOTHESIS:"):
                entities = [args["aggregate_key"].split(":", 1)[1]]
        elif symbol == _S + ".insert_receipt":
            entities = [revisions[r] for r in args["receipt"].subject_refs if r in revisions]
        else:
            record = args["value"]
            name = frame.f_code.co_name
            if symbol.startswith(_I + "."):
                data["investigation" if name == "create" else "investigation_audit"] = (
                    record.model_dump(mode="json")
                )
                entities = [record.investigation_id]
            elif name in {"add_hypothesis", "add_portfolio"}:
                data["main"] = record.model_dump(mode="json")
                entities = [
                    record.portfolio_id if name == "add_portfolio" else record.hypothesis_id
                ]
            elif name == "append_audit":
                data["audits"].append(record.model_dump(mode="json"))
                entities = [record.hypothesis_id]
            else:
                data["aux"] = record.model_dump(mode="json")
                field = {
                    "add_assumption": "assumption_id",
                    "add_prediction": "prediction_id",
                    "add_relation": "relation_id",
                }[name]
                entities = [getattr(record, field)]
        data["calls"].extend(
            {"symbol": symbol, "entity": entity, "source": frame.f_code.co_filename}
            for entity in entities
        )

    prior = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(prior)


async def _dispatch(
    runtime: Any,
    ctx: dict[str, Any],
    kind: str,
    key: str,
    patcher: pytest.MonkeyPatch,
    *,
    fault: bool,
) -> tuple[Any, dict[str, Any]]:
    command = request(ctx["method"], key, ctx["payload"])
    data: dict[str, Any] = {
        "calls": [],
        "audits": [],
        "blob_reads": [],
        "hits": [],
        "request": command.model_dump(mode="json"),
    }
    audit_original, blob_original = (
        SqliteHypothesisStore.append_audit,
        ContentAddressedObjectStore.read,
    )
    project = ctx["project"]

    def active() -> bool:
        op = current_operation.get()
        return op is not None and (op.method, op.idempotency_key, op.project_id) == (
            ctx["method"],
            key,
            project,
        )

    def blob(store: ContentAddressedObjectStore, digest: str) -> bytes:
        result = blob_original(store, digest)
        if active() and digest == ctx.get("contract_digest"):
            data["blob_reads"].append(
                {
                    "digest": digest,
                    "ambient": vars(transaction_module)["_AMBIENT"].get() is not None,
                }
            )
        return result

    def audit(store: SqliteHypothesisStore, record: Any) -> None:
        audit_original(store, record)
        main = data.get("main")
        if not fault or not active() or main is None or record.project_id != project:
            return
        subject = main["portfolio_id" if _is_portfolio(kind) else "hypothesis_id"]
        event = {
            "assumption": "hypothesis/assumptionChanged",
            "prediction": "hypothesis/predictionBound",
            "relation_add": "hypothesis/relationChanged",
            "relation_remove": "hypothesis/relationChanged",
            "revalidate": "hypothesis/portfolioUpdated",
            "counter": "hypothesis/counterevidenceRequested",
        }[kind]
        if record.event_type != event:
            return
        if kind == "relation_remove":
            matches = record.hypothesis_id == ctx["relation"][
                "source_hypothesis_id"
            ] and record.payload == {
                "reason": ctx["payload"]["reason"],
                "relation_id": ctx["relation"]["relation_id"],
            }
        elif kind == "revalidate":
            matches = record.hypothesis_id == subject and record.payload == {
                "trigger_reason": ctx["payload"]["trigger_reason"]
            }
        else:
            matches = record.hypothesis_id == subject and record.payload == {
                "revision_digest": main["revision_digest"]
            }
        if not matches:
            return
        observed: dict[str, Any] = {}
        with read_connection(runtime.ledger.engine) as connection:

            def select_rows(name: str, **fields: Any) -> list[dict[str, Any]]:
                table = metadata.tables[name]
                return [
                    dict(r)
                    for r in connection.execute(
                        select(table).where(*(table.c[k] == v for k, v in fields.items()))
                    ).mappings()
                ]

            revision_id = main[
                "portfolio_revision_id" if _is_portfolio(kind) else "hypothesis_revision_id"
            ]
            for table, filters in {
                "semantic_revisions": {"revision_id": revision_id},
                "working_heads": {"aggregate_key": "HYPOTHESIS:" + subject},
                "research_identities": {"owner_revision": main["revision_digest"]},
                "hypothesis_audit": {"audit_id": record.audit_id},
                ("hypothesis_portfolios" if _is_portfolio(kind) else "hypothesis_records"): {
                    "revision_digest": main["revision_digest"]
                },
            }.items():
                observed[table] = select_rows(table, project_id=project, **filters)
            revisions = observed["semantic_revisions"]
            observed["entity_snapshots"] = (
                []
                if not revisions
                else select_rows(
                    "entity_snapshots", project_id=project, snapshot_id=revisions[0]["snapshot_id"]
                )
            )
            observed["receipts"] = [
                r
                for r in select_rows("receipts", project_id=project)
                if revision_id in json.loads(r["payload_json"])["subject_refs"]
            ]
            observed["main_audit"] = [
                r
                for r in select_rows("hypothesis_audit", project_id=project, hypothesis_id=subject)
                if json.loads(r["payload_json"]).get("revision_digest") == main["revision_digest"]
            ]
            if kind in {"assumption", "prediction", "relation_add", "relation_remove"}:
                aux = data["aux"]
                table, field = {
                    "assumption": ("hypothesis_assumptions", "assumption_id"),
                    "prediction": ("hypothesis_predictions", "prediction_id"),
                    "relation_add": ("hypothesis_relations", "relation_revision_id"),
                    "relation_remove": ("hypothesis_relations", "relation_revision_id"),
                }[kind]
                observed["aux"] = select_rows(table, project_id=project, **{field: aux[field]})
                if kind == "prediction":
                    observed["prediction_revision"] = select_rows(
                        "semantic_revisions", project_id=project, entity_id=aux["prediction_id"]
                    )
                    observed["prediction_head"] = select_rows(
                        "working_heads",
                        project_id=project,
                        aggregate_key="HYPOTHESIS:" + aux["prediction_id"],
                    )
                    ids = {r["revision_id"] for r in observed["prediction_revision"]}
                    observed["prediction_identity"] = select_rows(
                        "research_identities",
                        project_id=project,
                        owner_revision=aux["prediction_digest"],
                    )
                    observed["prediction_snapshot"] = (
                        select_rows(
                            "entity_snapshots",
                            project_id=project,
                            snapshot_id=observed["prediction_revision"][0]["snapshot_id"],
                        )
                        if observed["prediction_revision"]
                        else []
                    )
                    observed["prediction_receipt"] = [
                        r
                        for r in select_rows("receipts", project_id=project)
                        if ids.intersection(json.loads(r["payload_json"])["subject_refs"])
                    ]
            if kind == "counter":
                inv = data["investigation"]
                observed["investigation"] = select_rows(
                    "investigations", project_id=project, investigation_id=inv["investigation_id"]
                )
                observed["investigation_audit"] = select_rows(
                    "investigation_audit",
                    project_id=project,
                    audit_id=data["investigation_audit"]["audit_id"],
                )
            data["hits"].append(
                {
                    "audit_entity": record.hypothesis_id,
                    "main_entity": subject,
                    "event_type": record.event_type,
                    "payload": record.payload,
                    "rows": observed,
                    "transaction_active": connection.in_transaction(),
                }
            )
        raise RuntimeError("M3_AUXILIARY_LATE_AUDIT:" + kind)

    with patcher.context() as patch:
        patch.setattr(ContentAddressedObjectStore, "read", blob)
        patch.setattr(SqliteHypothesisStore, "append_audit", audit)
        with _observe(command, data):
            response = await runtime.bus.dispatch(command)
    data["response"] = response.model_dump(mode="json")
    return response, data


def _path(data: dict[str, Any], kind: str) -> None:
    main = data["main"]
    portfolio = _is_portfolio(kind)
    entity = main["portfolio_id" if portfolio else "hypothesis_id"]
    expected = [
        _H + ("._persist_portfolio" if portfolio else "._persist_hypothesis"),
        _H + "._commit_record",
        *_BASE,
        _T + (".add_portfolio" if portfolio else ".add_hypothesis"),
        _T + ".append_audit",
    ]
    actual = [c["symbol"] for c in data["calls"] if c["entity"] == entity]
    assert actual[: len(expected)] == expected
    assert actual[len(expected) :] == ([_T + ".append_audit"] if kind == "revalidate" else [])
    if kind == "prediction":
        prediction_id = data["aux"]["prediction_id"]
        assert [c["symbol"] for c in data["calls"] if c["entity"] == prediction_id] == [
            *_BASE,
            _T + ".add_prediction",
        ]
        assert data["blob_reads"] and all(not r["ambient"] for r in data["blob_reads"])


async def _check(
    runtime: Any, ctx: dict[str, Any], kind: str, result: dict[str, Any], key: str
) -> None:
    portfolio = _is_portfolio(kind)
    main = result["portfolio" if portfolio else "hypothesis"]
    field = "portfolio_id" if portfolio else "hypothesis_id"
    read = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/portfolio/read" if portfolio else "hypothesis/read",
                key,
                {"project_id": ctx["project"], field: main[field]},
            )
        )
    )
    assert read["portfolio" if portfolio else "hypothesis"] == main
    if kind in {"relation_add", "relation_remove"}:
        assert [
            r for r in read["relations"] if r["relation_id"] == result["relation"]["relation_id"]
        ] == [result["relation"]]
    if kind == "assumption":
        assert [
            r
            for r in read["assumptions"]
            if r["assumption_id"] == result["assumption"]["assumption_id"]
        ] == [result["assumption"]]
    assert (
        runtime.ledger.read_heads(ctx["project"])["HYPOTHESIS:" + main[field]]
        == main["revision_digest"]
    )
    assert (
        main["portfolio_revision_id" if portfolio else "hypothesis_revision_id"]
        in result["commit"]["receipt"]["subject_refs"]
    )
    if kind == "prediction":
        prediction = result["prediction"]
        assert prediction["prediction_id"] in main["prediction_refs"]
        assert (
            runtime.ledger.read_heads(ctx["project"])["HYPOTHESIS:" + prediction["prediction_id"]]
            == prediction["prediction_digest"]
        )
        assert (
            prediction["prediction_fit"] == "NOT_OBSERVED"
            and prediction["observation_bound"] is False
        )
        read = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/prediction/read",
                    key + "-prediction",
                    {"project_id": ctx["project"], "prediction_id": prediction["prediction_id"]},
                )
            )
        )
        assert read["prediction"] == prediction
    if kind == "assumption":
        assert result["assumption"]["status"] == "UNVALIDATED"
        assert result["assumption"]["testability"] == "NOT_ASSESSED"
        assert result["assumption"]["assumption_id"] in main["assumption_refs"]
    if kind == "counter":
        inv = result["investigation"]
        assert inv["investigation_id"] in main["counterevidence_queries"]
        assert inv["scope"]["hypothesis_id"] == main["hypothesis_id"]
        assert inv["target_object_id"] == ctx["object"] and inv["target_hypothesis_id"] is None
        assert (
            inv["execution_state"] == "IDLE"
            and inv["current_wave"] == inv["observation_count"] == 0
        )
        assert main["development_stage"] == ctx["current"]["development_stage"]
        read = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/read",
                    key + "-investigation",
                    {"project_id": ctx["project"], "investigation_id": inv["investigation_id"]},
                )
            )
        )
        assert read["investigation"] == inv and read["search_intents"] == read["leads"] == []
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/audit/read",
                    key + "-investigation-audit",
                    {"project_id": ctx["project"], "investigation_id": inv["investigation_id"]},
                )
            )
        )
        assert len(audit["records"]) == 1
        assert audit["records"][0]["event_type"] == "investigation/started"


@pytest.mark.parametrize("kind", _KINDS)
async def test_auxiliary_late_audit_rolls_back_all_participants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime, ctx = await _prepare(tmp_path, kind)
    command = request(ctx["method"], "74-fault-" + kind, ctx["payload"])
    before = snapshot(runtime.ledger.engine)
    try:
        response, data = await _dispatch(
            runtime, ctx, kind, command.params.meta.idempotency_key, monkeypatch, fault=True
        )
        after = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        data.update(before=before, after=after, reopened=restarted, expected_reads=ctx["reads"])
        _save("fault-" + kind + ".json", data)
        _path(data, kind)
        assert response.error is not None and len(data["hits"]) == 1
        hit = data["hits"][0]
        assert hit["transaction_active"] and all(len(rows) == 1 for rows in hit["rows"].values())
        assert restarted == after
        allowed = failed_command_allowances(
            reopened.ledger.engine, command, expected_reads=ctx["reads"]
        )
        assert_phase_delta(before, restarted, allowed)
        if kind == "relation_remove":
            prior = SqliteHypothesisStore(reopened.ledger.engine, reopened.ledger).read_relation(
                ctx["relation"]["relation_id"]
            )
            assert prior is not None and prior.active is True
        recovered = value(
            await reopened.bus.dispatch(
                request(ctx["method"], "74-recover-" + kind, ctx["payload"])
            )
        )
        await _check(reopened, ctx, kind, recovered, "recovered")
        data.update(recovered=recovered, verdict="PASS")
        _save("fault-" + kind + ".json", data)
    finally:
        reopened.close()


async def test_prediction_assumption_and_investigation_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ctx = await _prepare(tmp_path, "prediction")
    try:
        response, traced = await _dispatch(
            runtime, ctx, "prediction", "74-chain-prediction", monkeypatch, fault=False
        )
        _path(traced, "prediction")
        predicted = value(response)
        await _check(runtime, ctx, "prediction", predicted, "predicted")
        ctx["current"] = predicted["hypothesis"]
        prediction = predicted["prediction"]
        old_predictions = snapshot(runtime.ledger.engine)["hypothesis_predictions"]
        assumption = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/assumption/add", "74-chain-assumption", _assumption_payload(ctx)
                )
            )
        )
        await _check(runtime, ctx, "assumption", assumption, "assumption")
        assert assumption["hypothesis"]["prediction_refs"] == []
        assert prediction["prediction_id"] in assumption["hypothesis"]["invalidated_refs"]
        assert snapshot(runtime.ledger.engine)["hypothesis_predictions"] == old_predictions
        historical = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/prediction/read",
                    "historical-prediction",
                    {"project_id": ctx["project"], "prediction_id": prediction["prediction_id"]},
                )
            )
        )
        assert historical["prediction"] == prediction
        ctx["current"] = assumption["hypothesis"]
        counter = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/counterevidence/request",
                    "74-chain-counter",
                    {
                        "project_id": ctx["project"],
                        "hypothesis_id": ctx["current"]["hypothesis_id"],
                        "source_scope": ["independent sources"],
                    },
                )
            )
        )
        await _check(runtime, ctx, "counter", counter, "counter")
        _save(
            "normal-prediction-assumption-counter.json",
            {
                "prediction": predicted,
                "assumption": assumption,
                "counter": counter,
                "trace": traced,
                "old_prediction_preserved": True,
                "verdict": "PASS",
            },
        )
    finally:
        runtime.close()


async def test_portfolio_relation_lifecycle_and_replay(tmp_path: Path) -> None:
    runtime, ctx = await _prepare(tmp_path, "relation_add")
    evidence: dict[str, Any] = {}
    try:
        commands = [request("hypothesis/relation/add", "74-chain-add", _relation_payload(ctx))]
        added = value(await runtime.bus.dispatch(commands[-1]))
        ctx.update(portfolio=added["portfolio"], relation=added["relation"])
        await _check(runtime, ctx, "relation_add", added, "added")
        after = snapshot(runtime.ledger.engine)
        assert (
            value(await runtime.bus.dispatch(commands[-1])) == added
            and snapshot(runtime.ledger.engine) == after
        )
        revalidate = request(
            "hypothesis/portfolio/revalidate",
            "74-chain-revalidate",
            {
                "project_id": ctx["project"],
                "portfolio_id": ctx["portfolio"]["portfolio_id"],
                "trigger_reason": "74-chain-refresh",
            },
        )
        validated = value(await runtime.bus.dispatch(revalidate))
        ctx["portfolio"] = validated["portfolio"]
        await _check(runtime, ctx, "revalidate", validated, "validated")
        after = snapshot(runtime.ledger.engine)
        assert (
            value(await runtime.bus.dispatch(revalidate)) == validated
            and snapshot(runtime.ledger.engine) == after
        )
        remove = request(
            "hypothesis/relation/remove",
            "74-chain-remove",
            {
                "project_id": ctx["project"],
                "relation_id": ctx["relation"]["relation_id"],
                "reason": "74-chain-end",
                "evidence_refs": ctx["spans"],
                "expected_portfolio_revision": ctx["portfolio"]["revision_digest"],
            },
        )
        ended = value(await runtime.bus.dispatch(remove))
        await _check(runtime, ctx, "relation_remove", ended, "ended")
        assert (
            ended["relation"]["active"] is False
            and ended["relation"]["supersedes_revision_digest"]
            == added["relation"]["revision_digest"]
        )
        assert ended["portfolio"]["relation_refs"] == validated["portfolio"]["relation_refs"]
        terminal = snapshot(runtime.ledger.engine)
        assert (
            value(await runtime.bus.dispatch(remove)) == ended
            and snapshot(runtime.ledger.engine) == terminal
        )
        evidence.update(added=added, revalidated=validated, ended=ended, terminal=terminal)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == terminal
        relations = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/relation/list",
                    "relations-after-reopen",
                    {
                        "project_id": ctx["project"],
                        "portfolio_id": ended["portfolio"]["portfolio_id"],
                    },
                )
            )
        )
        assert relations["relations"] == [ended["relation"]]
        history = [
            json.loads(r)["content_json"]
            for r in terminal["hypothesis_relations"]
            if json.loads(r)["relation_id"] == ended["relation"]["relation_id"]
        ]
        assert len(history) == 2 and any(json.loads(r)["active"] for r in history)
        evidence.update(reopened_equal=True, readback=relations, verdict="PASS")
        _save("normal-portfolio-relations.json", evidence)
    finally:
        reopened.close()
