"""Explicit v5 normal entry, pinned reads/reopen, and failure injection at the existing UoW."""

from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.hypothesis_grounding_runtime import GroundingGenerator
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.test_hypothesis_contract_v3_run import turn_on
from tests.integration.test_trace_origin import Rpc, opened

from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.application.services.hypothesis_grounding_v5 import grounding_generator_contract
from thoth.application.workflows import thread_cycle
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.research_execution import research_work
from thoth.domain.research_identity import ResearchFamily, decode_research


def expected_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for h in result["portfolio"]["hypotheses"]
        for t in h["discriminating_tests"]
        for row in t["expected_by_hypothesis"]
    ]


def hypothesis_heads(runtime: Any) -> dict[str, str]:
    return {
        key: value
        for key, value in runtime.ledger.read_heads("p").items()
        if key.startswith("HYPOTHESIS:")
    }


@pytest.mark.asyncio
async def test_explicit_v5_persists_and_reopens_three_read_contracts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(thread_cycle, "generator_contract_for_work", grounding_generator_contract)
    model = GroundingGenerator()
    runtime, rpc = await opened(tmp_path, model)
    try:
        await turn_on(rpc)
        admission = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate the cause",
            response_language="en",
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(admission["operation_id"])
        assert operation.state.value == "SUCCEEDED", operation.error
        read = await rpc(
            "thread/read",
            project_id="p",
            thread_id=admission["thread_id"],
            view="FULL",
            expected_result_contract="v5",
        )
        result = read["current_result"]["result"]
        assert result["hypothesis_contract_version"] == "hypothesis_portfolio.v5"
        rows = expected_rows(result)
        assert rows and all(
            r["relation_assessment"]["assessment_status"] == "UNASSESSED" for r in rows
        )
        linked = next(row for row in rows if row["basis"])
        assert linked["basis"] == [item["span_id"] for item in linked["source_bindings"]]
        assert linked["target"]["owner_ref"]["project_id"] == "p"
        target = linked["target"]
        target_revision = runtime.ledger.read_revision_by_digest(
            "p", target["owner_ref"]["revision_digest"]
        )
        target_record = runtime.ledger.read_snapshot(target_revision.snapshot_id).content
        stored_group = next(
            g
            for g in target_record["generation_details"]["expected_results"]
            if g["test_id"] == target["test_id"]
        )
        stored_row = next(
            r for r in stored_group["rows"] if r["hypothesis_id"] == linked["hypothesis_id"]
        )
        assert all(linked[key] == value for key, value in stored_row.items())
        selected = (
            "thread/result/read",
            {
                "thread_id": admission["thread_id"],
                "request_revision_digest": read["current_result"]["request_ref"]["revision_digest"],
            },
        )
        original = canonical_payload(operation)
        calls_before_reads = len(model.calls)
        for method, args in [
            ("operation/result/read", {"operation_id": admission["operation_id"]}),
            selected,
        ]:
            typed = await rpc(method, project_id="p", expected_result_contract="v5", **args)
            v4 = await rpc(method, project_id="p", expected_result_contract="v4", **args)
            legacy = await rpc(method, project_id="p", **args)
            assert typed["result"]["portfolio"] == result["portfolio"]
            assert v4["expected_result_view"] == "V4_COMPATIBILITY_PROJECTION"
            assert all(
                "grounding" not in row and "knowledge_state" in row
                for row in expected_rows(v4["result"])
            )
            assert all("knowledge_state" not in row for row in expected_rows(legacy["result"]))
            if "manifest" in typed:
                assert (
                    canonical_payload(typed["manifest"])
                    == canonical_payload(v4["manifest"])
                    == canonical_payload(legacy["manifest"])
                )
            wrong_scope = await rpc.raw(
                method, project_id="other", expected_result_contract="v5", **args
            )
            assert wrong_scope.error is not None and wrong_scope.result is None
        assert len(model.calls) == calls_before_reads
        assert canonical_payload(runtime.bus.read_operation(admission["operation_id"])) == original
        saved = []
        for digest in hypothesis_heads(runtime).values():
            revision = runtime.ledger.read_revision_by_digest("p", digest)
            snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
            if snapshot.content.get("schema_version") != "1.3.0":
                continue
            decoded = decode_research(revision, snapshot)
            assert decoded.identity.schema_family in (
                ResearchFamily.HYPOTHESIS,
                ResearchFamily.HYPOTHESIS_PORTFOLIO,
            )
            details = snapshot.content["generation_details"]
            assert details["stage_inputs"]["GENERATOR"]["max_output_tokens"] == 8000
            assert details["generating_request_ref"] == read["current_result"]["request_ref"]
            saved.append((snapshot.snapshot_id, canonical_payload(snapshot.content)))
        assert len(saved) >= 3
        assert {
            call.max_output_tokens
            for call in model.calls
            if call.role == ModelRole.HYPOTHESIS_GENERATOR
        } == {8000}
        assert {
            call.max_output_tokens
            for call in model.calls
            if call.role == ModelRole.HYPOTHESIS_TABLE_FILLER
        } == {3000}
        calls = len(model.calls)
        runtime.close()
        runtime = create_runtime(
            tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
        )
        rpc = Rpc(runtime)
        rpc.count = 100
        restored = await rpc(
            selected[0], project_id="p", expected_result_contract="v5", **selected[1]
        )
        assert restored["result"]["portfolio"] == result["portfolio"] and len(model.calls) == calls
        for identifier, data in saved:
            assert canonical_payload(runtime.ledger.read_snapshot(identifier).content) == data
        for language in ("ko", None):
            before = len(model.calls)
            following = await rpc(
                "thread/input",
                project_id="p",
                thread_id=admission["thread_id"],
                contract_version=2,
                instruction="Inspect the next condition",
                **({"response_language": language} if language is not None else {}),
            )
            await runtime.bus.drain()
            follow_op = runtime.bus.read_operation(following["operation_id"])
            assert follow_op.state.value == "SUCCEEDED", follow_op.error
            assert follow_op.result["hypothesis_contract_version"] == "hypothesis_portfolio.v5"
            assert {call.response_language for call in model.calls[before:]} == {language}
            for digest in hypothesis_heads(runtime).values():
                revision = runtime.ledger.read_revision_by_digest("p", digest)
                snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
                if snapshot.content.get("schema_version") == "1.3.0":
                    assert snapshot.content["generation_details"]["response_language"] == language
        for identifier, data in saved:
            assert canonical_payload(runtime.ledger.read_snapshot(identifier).content) == data
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_default_v5_and_saved_off_v2_keep_distinct_budgets(tmp_path: Path) -> None:
    # No selector override: this is the actual default after the approved consumer rollout.
    model = GroundingGenerator()
    runtime, rpc = await opened(tmp_path, model)
    try:
        enabled = await rpc("model/callSettings/read", project_id="p")
        assert enabled["hypothesis_contract_v3"] is True
        first = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate the cause",
            response_language="en",
        )
        await runtime.bus.drain()
        op = runtime.bus.read_operation(first["operation_id"])
        assert op.state.value == "SUCCEEDED", op.error
        assert op.result["hypothesis_contract_version"] == "hypothesis_portfolio.v5"
        await rpc(
            "model/callSettings/update",
            project_id="p",
            hypothesis_contract_v3=False,
            expected_digest=enabled["settings_digest"],
        )
        second = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate without the prediction table",
            response_language="en",
        )
        await runtime.bus.drain()
        op = runtime.bus.read_operation(second["operation_id"])
        assert op.state.value == "SUCCEEDED", op.error
        generators = [c for c in model.calls if c.role == ModelRole.HYPOTHESIS_GENERATOR]
        assert [(c.prompt_version, c.max_output_tokens) for c in generators] == [
            ("hypothesis_portfolio.v5", 8000),
            ("hypothesis_portfolio.v2", 4000),
        ]
        assert all(
            "expected_by_hypothesis" not in test
            for owner in op.result["portfolio"]["hypotheses"]
            for test in owner["discriminating_tests"]
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_capture_failure_is_publication_hold_without_hypothesis_writes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(thread_cycle, "generator_contract_for_work", grounding_generator_contract)
    runtime, rpc = await opened(tmp_path, GroundingGenerator(wrong_contract=True))
    try:
        before = hypothesis_heads(runtime)
        admission = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate the cause",
            response_language="en",
        )
        await runtime.bus.drain()
        op = runtime.bus.read_operation(admission["operation_id"])
        assert op.state.value == "SUCCEEDED", op.error
        assert op.result["terminal_reason"] == "GROUNDING_PUBLICATION_BINDING_MISMATCH"
        assert op.result["research_state"] == "PARTIAL"
        assert hypothesis_heads(runtime) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_canonical_and_projection_writes_roll_back_together(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(thread_cycle, "generator_contract_for_work", grounding_generator_contract)
    runtime, rpc = await opened(tmp_path, GroundingGenerator())
    try:
        before = hypothesis_heads(runtime)
        projection = SqliteHypothesisStore(runtime.ledger.engine)
        before_records = tuple(canonical_payload(h) for h in projection.list_hypotheses("p"))
        written = []
        original_add = SqliteHypothesisStore.add_hypothesis

        def add_hypothesis(self: Any, value: Any) -> None:
            written.append(value.hypothesis_revision_id)
            original_add(self, value)

        def fail_projection(self: Any, value: Any) -> None:
            del self
            written.append(value.portfolio_revision_id)
            raise RuntimeError("injected portfolio projection failure")

        monkeypatch.setattr(SqliteHypothesisStore, "add_hypothesis", add_hypothesis)
        monkeypatch.setattr(SqliteHypothesisStore, "add_portfolio", fail_projection)
        admission = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate the cause",
            response_language="en",
        )
        await runtime.bus.drain()
        op = runtime.bus.read_operation(admission["operation_id"])
        assert op.state.value == "FAILED"
        assert hypothesis_heads(runtime) == before
        assert (
            tuple(canonical_payload(h) for h in projection.list_hypotheses("p")) == before_records
        )
        assert written
        assert all(
            runtime.ledger.read_revision_by_id("p", identifier) is None for identifier in written
        )
        assert all(
            not set(written).intersection(receipt.subject_refs)
            for receipt in runtime.ledger.read_receipts("p")
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_capture_mismatch_before_cycle_commit_is_hold_without_new_hypothesis_records(
    tmp_path: Path, monkeypatch: Any
) -> None:
    model = GroundingGenerator()
    runtime, rpc = await opened(tmp_path, model)
    original = thread_cycle.ThreadCycleService._save_cycle

    def corrupt_before_save(self: Any, **kwargs: Any):
        work = research_work.get()
        assert work is not None and work.hypothesis_grounding is not None
        owner_id, test_id, hypothesis_id, linked = next(
            (h.hypothesis_id, t.test_id, row.hypothesis_id, row.basis[0])
            for h in work.hypothesis_grounding.portfolio.hypotheses
            for t in h.discriminating_tests
            for row in t.expected_by_hypothesis
            if row.basis
        )
        stage = work.hypothesis_grounding.row_stages[(owner_id, test_id, hypothesis_id)]
        capture = work.grounding_stages[stage]
        damaged = tuple(
            span.model_copy(update={"text_sha256": "0" * 64}) if span.span_id == linked else span
            for span in capture.evidence
        )
        # Corrupt server provenance, not the source packet checked by the existing authority fence.
        work.grounding_stages[stage] = capture.model_copy(update={"evidence": damaged})
        return original(self, **kwargs)

    try:
        before = hypothesis_heads(runtime)
        monkeypatch.setattr(thread_cycle.ThreadCycleService, "_save_cycle", corrupt_before_save)
        admission = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="Investigate the cause",
            response_language="en",
        )
        await runtime.bus.drain()
        op = runtime.bus.read_operation(admission["operation_id"])
        assert op.state.value == "SUCCEEDED", op.error
        assert op.result["terminal_reason"] == "GROUNDING_PUBLICATION_BINDING_MISMATCH"
        assert op.result["research_state"] == "PARTIAL"
        assert "hypothesis_revision_refs" not in op.result
        assert hypothesis_heads(runtime) == before
        assert not SqliteHypothesisStore(runtime.ledger.engine).list_hypotheses("p")
    finally:
        runtime.close()
