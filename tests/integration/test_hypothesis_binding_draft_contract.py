"""Observe draft construction only inside the normal producer; do not fabricate missing bindings."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    prepare_measurement,
    request,
    value,
)

from thoth.application.services.hypothesis_service import HypothesisService
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.deferred import current_operation

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step78-20261014"
_BIND_KEYS = (
    "test_binding_id",
    "project_id",
    "prediction_id",
    "execution_ref",
    "observation_refs",
    "test_validity_assessment_ref",
    "test_validity",
    "prediction_fit",
    "appraisal_mutation_allowed",
    "assessment_digest",
    "prediction_digest",
    "created_at",
)


class RecordingClock:
    def __init__(self, original: ClockPort, events: list[dict[str, Any]]):
        self.original, self.events = original, events

    def now(self) -> datetime:
        result = self.original.now()
        self.events.append({"call": "clock", "value": result})
        return result


class RecordingIDs:
    def __init__(self, original: IdGeneratorPort, events: list[dict[str, Any]]):
        self.original, self.events = original, events

    def new(self, prefix: str) -> str:
        result = self.original.new(prefix)
        self.events.append({"call": "id", "prefix": prefix, "value": result})
        return result


async def test_binding_and_appraisal_drafts_use_original_dependencies_in_native_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    observed: list[dict[str, Any]] = []
    original_bind, original_appraise = HypothesisService.bind_test, HypothesisService.appraise

    def wrap(kind: str, original: Any) -> Any:
        def invoke(service: Any, current: Any, **kwargs: Any) -> Any:
            operation = current_operation.get()
            if (
                operation is None
                or operation.project_id != project
                or operation.idempotency_key not in {"78-native", "78-reuse"}
            ):
                return original(service, current, **kwargs)
            events: list[dict[str, Any]] = []
            entry: dict[str, Any] = {
                "kind": kind,
                "operation_key": operation.idempotency_key,
                "events": events,
            }
            attributes: dict[str, Any] = service.__dict__
            clock, ids = attributes["_clock"], attributes["_ids"]
            had_digest, previous_digest = "_aux_digest" in attributes, attributes.get("_aux_digest")
            digest = service._aux_digest

            def record_digest(label: str, draft: dict[str, object]) -> str:
                events.append({"call": "digest", "kind": label})
                entry["draft"] = dict(draft)
                entry["keys"] = list(draft)
                if label == "TEST_BINDING":
                    entry["observation_identity"] = (
                        draft["observation_refs"] is kwargs["observation_refs"]
                    )
                if label == "APPRAISAL":
                    entry["scope_identity"] = draft["appraisal_scope"] is kwargs["appraisal_scope"]
                return digest(label, draft)

            attributes.update(
                _clock=RecordingClock(clock, events),
                _ids=RecordingIDs(ids, events),
                _aux_digest=record_digest,
            )
            try:
                result = original(service, current, **kwargs)
                record = result if kind == "bind" else result[0]
                entry["record"] = record.model_dump(mode="json")
                entry["returned_created_at"] = record.created_at
                observed.append(entry)
                return result
            finally:
                attributes.update(_clock=clock, _ids=ids)
                if had_digest:
                    attributes["_aux_digest"] = previous_digest
                else:
                    attributes.pop("_aux_digest", None)

        return invoke

    try:
        with monkeypatch.context() as patch:
            patch.setattr(HypothesisService, "bind_test", wrap("bind", original_bind))
            patch.setattr(HypothesisService, "appraise", wrap("appraise", original_appraise))
            produced = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input", "78-native", {"project_id": project, "thread_id": thread}
                    )
                )
            )
            lifecycle = produced["r2_closed_loop"]["test_lifecycle"]
            assessment = lifecycle["assessments"][0]
            same = value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/test/bind",
                        "78-reuse",
                        {
                            "project_id": project,
                            "prediction_id": assessment["prediction_id"],
                            "execution_ref": assessment["execution_ref"],
                            "observation_refs": assessment["observation_refs"],
                            "test_validity_assessment_ref": assessment["assessment_id"],
                            "test_validity": assessment["test_validity"],
                            "prediction_fit": assessment["prediction_fit"],
                        },
                    )
                )
            )
        _OUT.mkdir(parents=True, exist_ok=True)
        (_OUT / "native-draft-observation.json").write_text(
            json.dumps(
                {"observed": observed, "lifecycle": lifecycle, "reused": same},
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        binds = [e for e in observed if e["kind"] == "bind" and e["operation_key"] == "78-native"]
        appraisals = [e for e in observed if e["kind"] == "appraise"]
        reuses = [e for e in observed if e["operation_key"] == "78-reuse"]
        assert len(binds) == len(appraisals) == len(lifecycle["predictions"]) == 2
        assert len(reuses) == 1 and reuses[0]["events"] == []
        assert same["test_binding"]["test_binding_id"] == binds[0]["record"]["test_binding_id"]
        for entry in binds:
            assert [e["call"] for e in entry["events"]] == ["id", "clock", "digest"]
            assert entry["events"][0]["prefix"] == "hypothesis-test-binding"
            assert entry["record"]["test_binding_id"] == entry["events"][0]["value"]
            assert entry["returned_created_at"] == entry["events"][1]["value"]
            assert tuple(entry["keys"]) == _BIND_KEYS and entry["observation_identity"] is True
        for entry in appraisals:
            assert [e["call"] for e in entry["events"][:3]] == ["id", "clock", "digest"]
            assert entry["events"][0]["prefix"] == "hypothesis-appraisal"
            assert entry["record"]["appraisal_id"] == entry["events"][0]["value"]
            assert entry["returned_created_at"] == entry["events"][1]["value"]
            assert (
                entry["scope_identity"] is True
                and entry["record"]["substantive_update_applied"] is True
            )
        assert {e["record"]["appraisal"] for e in appraisals} == {
            "EVIDENCE_FAVORS",
            "EVIDENCE_AGAINST",
        }
        assert len(sandbox.seen_specs) == 1
    finally:
        runtime.close()
