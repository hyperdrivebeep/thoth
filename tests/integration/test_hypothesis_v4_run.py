"""The explicit English request reaches generation, filling, canonical storage and normal read."""

from dataclasses import replace
from pathlib import Path

import pytest
from tests.fixtures.legacy_hypothesis_contract import legacy_generation_contract
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.test_hypothesis_contract_v3_run import GeneratorModel, turn_on
from tests.integration.test_trace_origin import Rpc, opened

from thoth.application.workflows import thread_cycle
from thoth.apps.restore_profiles import restore_profiles
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisPortfolioV3, HypothesisTableFill
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.research_identity import ResearchFamily, decode_research


class LanguageGenerator(GeneratorModel):
    async def structured(self, request):
        if request.output_model is HypothesisPortfolioV4:
            result = await super().structured(replace(request, output_model=HypothesisPortfolioV3))
            self.calls[-1] = request
            shaped = result.output.model_dump()
            for hypothesis in shaped["hypotheses"]:
                for test in hypothesis["discriminating_tests"]:
                    for row in test["expected_by_hypothesis"]:
                        unknown = row["expected"] == "모름"
                        row.update(
                            knowledge_state="UNKNOWN" if unknown else "PREDICTED",
                            expected=None if unknown else row["expected"],
                        )
            return replace(result, output=HypothesisPortfolioV4.model_validate(shaped))
        if request.output_model is HypothesisTableFillV4:
            result = await self.fill(replace(request, output_model=HypothesisTableFill))
            self.calls[-1] = request
            shaped = result.output.model_dump()
            for test in shaped["tests"]:
                for row in test["rows"]:
                    row["knowledge_state"] = "PREDICTED"
            return replace(result, output=HypothesisTableFillV4.model_validate(shaped))
        return await super().structured(request)


@pytest.fixture(autouse=True)
def explicit_legacy_v4_generator(monkeypatch):
    # This module exercises the old codec, not the newly approved default-v5 selector.
    monkeypatch.setattr(thread_cycle, "generator_contract_for_work", legacy_generation_contract)


@pytest.mark.asyncio
async def test_english_v4_normal_entry_and_canonical_read(tmp_path: Path):
    model = LanguageGenerator()
    runtime, rpc = await opened(tmp_path, model)
    try:
        await turn_on(rpc)
        admission = await rpc(
            "thread/start",
            project_id="p",
            contract_version=2,
            problem="원인을 조사해 주세요",
            response_language="en",
        )
        await runtime.bus.drain()
        read = await rpc(
            "thread/read",
            project_id="p",
            thread_id=admission["thread_id"],
            view="FULL",
            expected_result_contract="v4",
        )
        operation = runtime.bus.read_operation(admission["operation_id"])
        assert operation.state.value == "SUCCEEDED", operation.error
        assert read["request"]["response_language"] == "en"
        output = read["current_result"]["result"]
        assert output["hypothesis_table_fill"]["contract_version"] == "hypothesis_table_filler.v5"
        assert output["hypothesis_table_fill"]["state"] == "CALLED"
        assert all(
            c.context_pack.research_context["response_language"] == "en" for c in model.calls
        )
        assert (
            next(c for c in model.calls if c.role == ModelRole.HYPOTHESIS_GENERATOR).output_model
            is HypothesisPortfolioV4
        )
        rows = [
            row
            for h in output["portfolio"]["hypotheses"]
            for t in h["discriminating_tests"]
            for row in t["expected_by_hypothesis"]
        ]
        assert rows and all("knowledge_state" in row for row in rows)
        assert any(row["expected"] == "worse" for row in rows)
        original_operation = canonical_payload(
            runtime.bus.read_operation(admission["operation_id"])
        )
        for method, arguments in (
            ("operation/result/read", {"operation_id": admission["operation_id"]}),
            (
                "thread/result/read",
                {
                    "thread_id": admission["thread_id"],
                    "request_revision_digest": read["current_result"]["request_ref"][
                        "revision_digest"
                    ],
                },
            ),
        ):
            legacy = await rpc(method, project_id="p", **arguments)
            typed = await rpc(method, project_id="p", expected_result_contract="v4", **arguments)
            assert legacy["expected_result_view"] == "LEGACY_DISPLAY_PROJECTION"
            legacy_rows = [
                r
                for h in legacy["result"]["portfolio"]["hypotheses"]
                for t in h["discriminating_tests"]
                for r in t["expected_by_hypothesis"]
            ]
            assert all("knowledge_state" not in row for row in legacy_rows)
            assert typed["result"]["portfolio"] == output["portfolio"]
            if "manifest" in typed:
                assert canonical_payload(legacy["manifest"]) == canonical_payload(typed["manifest"])
        assert (
            canonical_payload(runtime.bus.read_operation(admission["operation_id"]))
            == original_operation
        )
        saved = []
        for key, digest in runtime.ledger.read_heads("p").items():
            if not key.startswith("HYPOTHESIS:"):
                continue
            revision = runtime.ledger.read_revision_by_digest("p", digest)
            snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
            if snapshot.content.get("schema_version") != "1.2.0":
                continue
            decoded = decode_research(revision, snapshot)
            assert decoded.identity.schema_family in (
                ResearchFamily.HYPOTHESIS,
                ResearchFamily.HYPOTHESIS_PORTFOLIO,
            )
            _, restored = restore_profiles().resolve(revision, snapshot)
            assert restored.schema_version == "1.2.0"
            saved.append((snapshot.snapshot_id, canonical_payload(snapshot.content)))
        assert len(saved) >= 3
        runtime.close()
        runtime = create_runtime(
            tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
        )
        rpc = Rpc(runtime)
        rpc.count = 100  # Reopened client uses new idempotency keys.
        # Consume the persisted v4 heads in the next normal research, not just a request-only run.
        for language in ("ko", "en", None):
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
            operation = runtime.bus.read_operation(following["operation_id"])
            assert operation.state.value == "SUCCEEDED", operation.error
            assert model.calls[before:]
            assert {call.response_language for call in model.calls[before:]} == {language}
        for snapshot_id, original in saved:
            assert canonical_payload(runtime.ledger.read_snapshot(snapshot_id).content) == original
    finally:
        runtime.close()
