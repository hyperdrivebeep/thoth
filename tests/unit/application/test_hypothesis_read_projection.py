"""Freeze the public handler's response builders before pure projection extraction."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from thoth.application.commands.hypotheses_full import HypothesisHandlers
from thoth.application.services.revision_service import CommitResult
from thoth.domain.hypothesis_full import HypothesisRecord


def record(refs: tuple[str, ...] = ()) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_revision_id="revision:1",
        hypothesis_id="hypothesis:1",
        project_id="p",
        object_id="object:1",
        portfolio_id="portfolio:1",
        statement="원문 12 ms",
        observed_problem="fixture",
        primary_intent="EXPLORATORY",
        intent_profile_refs=refs,
        evidence_basis="fixture",
        scope={},
        evidence_refs=(),
        revision_digest="a" * 64,
        created_at=datetime(2026, 10, 14, tzinfo=UTC),
    )


@pytest.mark.parametrize("refs", [(), ("profile:b", "profile:a")])
@pytest.mark.parametrize("via_instance", [False, True])
def test_summary_order_values_and_independent_list(
    refs: tuple[str, ...], via_instance: bool
) -> None:
    item = record(refs)
    original = item.model_dump(mode="json")
    owner = object.__new__(HypothesisHandlers) if via_instance else HypothesisHandlers
    value = owner._summary(item)  # pyright: ignore[reportPrivateUsage]
    expected = {
        "hypothesis_id": "hypothesis:1",
        "object_id": "object:1",
        "portfolio_id": "portfolio:1",
        "statement": "원문 12 ms",
        "primary_intent": "EXPLORATORY",
        "intent_profile_refs": list(refs),
        "development_stage": "DRAFT",
        "empirical_appraisal": "UNASSESSED",
        "freshness": "CURRENT",
        "revision_digest": "a" * 64,
    }
    assert value == expected and list(value) == list(expected)
    output_refs = value["intent_profile_refs"]
    assert isinstance(output_refs, list)
    output_refs.append("extra")
    assert item.model_dump(mode="json") == original
    assert owner._summary(item)["intent_profile_refs"] == list(refs)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("via_instance", [False, True])
def test_revision_result_json_sorted_diff_and_input_preservation(via_instance: bool) -> None:
    before = record()
    after = before.model_copy(
        update={
            "statement": "changed",
            "invalidated_refs": ("test:b", "prediction:a"),
            "development_stage": "GROUNDED_CANDIDATE",
            "freshness": "STALE",
        }
    )
    snapshots = [item.model_dump(mode="json") for item in (before, after)]
    commit = Mock(spec=CommitResult)
    commit.model_dump.return_value = {"disposition": "COMMITTED"}
    owner = object.__new__(HypothesisHandlers) if via_instance else HypothesisHandlers
    value = owner._revision_result(before, after, cast(CommitResult, commit))  # pyright: ignore[reportPrivateUsage]
    assert list(value) == [
        "hypothesis",
        "semantic_diff",
        "invalidated_predictions_tests_appraisals",
        "stage",
        "freshness",
        "commit",
    ]
    assert value["hypothesis"] == snapshots[1]
    assert value["semantic_diff"] == [
        {"path": "development_stage", "before": "DRAFT", "after": "GROUNDED_CANDIDATE"},
        {"path": "freshness", "before": "CURRENT", "after": "STALE"},
        {"path": "invalidated_refs", "before": [], "after": ["test:b", "prediction:a"]},
        {"path": "statement", "before": "원문 12 ms", "after": "changed"},
    ]
    assert value["stage"] == "GROUNDED_CANDIDATE" and value["freshness"] == "STALE"
    assert value["commit"] is commit.model_dump.return_value
    invalidated = value["invalidated_predictions_tests_appraisals"]
    assert isinstance(invalidated, list)
    invalidated.append("extra")
    assert [item.model_dump(mode="json") for item in (before, after)] == snapshots
    commit.model_dump.assert_called_once_with(mode="json")


class DumpProbe:
    invalidated_refs = ("test:b", "prediction:a")
    development_stage = "GROUNDED_CANDIDATE"
    freshness = "STALE"

    def __init__(
        self,
        name: str,
        events: list[str],
        payload: dict[str, JsonValue],
        error: RuntimeError | None = None,
    ) -> None:
        self.name, self.events, self.payload, self.error = name, events, payload, error

    def model_dump(self, *, mode: str) -> dict[str, JsonValue]:
        assert mode == "json"
        self.events.append(self.name)
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.mark.parametrize("failure", [None, "before", "after", "commit"])
def test_dump_order_missing_none_identity_and_same_exception(failure: str | None) -> None:
    events: list[str] = []
    error = RuntimeError("injected dump failure")
    before_data: dict[str, JsonValue] = {"z": "removed", "n": None, "a": [2, 1], "same": "same"}
    after_data: dict[str, JsonValue] = {"new_none": None, "b": "added", "a": [1, 2], "same": "same"}
    payloads: list[dict[str, JsonValue]] = [before_data, after_data, {"receipt": "receipt:1"}]
    snapshots = deepcopy(payloads)
    probes = [
        DumpProbe(name, events, payload, error if failure == name else None)
        for name, payload in zip(("before", "after", "commit"), payloads, strict=True)
    ]
    call = HypothesisHandlers._revision_result  # pyright: ignore[reportPrivateUsage]
    if failure:
        with pytest.raises(RuntimeError) as caught:
            call(
                cast(HypothesisRecord, probes[0]),
                cast(HypothesisRecord, probes[1]),
                cast(CommitResult, probes[2]),
            )
        assert caught.value is error
        assert (
            events
            == ["before", "after", "commit"][: ["before", "after", "commit"].index(failure) + 1]
        )
    else:
        value = call(
            cast(HypothesisRecord, probes[0]),
            cast(HypothesisRecord, probes[1]),
            cast(CommitResult, probes[2]),
        )
        assert events == ["before", "after", "commit"]
        assert value["hypothesis"] is after_data and value["commit"] is payloads[2]
        assert value["semantic_diff"] == [
            {"path": "a", "before": [2, 1], "after": [1, 2]},
            {"path": "b", "before": None, "after": "added"},
            {"path": "z", "before": "removed", "after": None},
        ]
        assert value["invalidated_predictions_tests_appraisals"] == ["test:b", "prediction:a"]
    assert payloads == snapshots


@pytest.mark.asyncio
@pytest.mark.parametrize("method,result_key", [("list", "hypotheses"), ("graph_read", "nodes")])
async def test_callers_keep_helper_lookup(
    monkeypatch: pytest.MonkeyPatch, method: str, result_key: str
) -> None:
    item = record()
    store = Mock()
    store.list_hypotheses.return_value = (item,)
    store.list_relations.return_value = ()
    owner = object.__new__(HypothesisHandlers)
    monkeypatch.setattr(owner, "_store", store, raising=False)
    project = Mock(return_value={"marker": "patched"})
    monkeypatch.setattr(HypothesisHandlers, "_summary", staticmethod(project))
    value = await getattr(owner, method)({"project_id": "p"})
    assert value[result_key] == [{"marker": "patched"}]
    project.assert_called_once_with(item)
