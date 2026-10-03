"""The metrics come from the stored selection records; what needs a person's answer stays null."""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from tests.integration.test_memory_selection import BUNDLE, context, service, stored

from thoth.domain.enums import MemoryKind

SCRIPT = Path(__file__).parents[2] / "scripts" / "memory_selection_metrics.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("memory_selection_metrics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_counts_and_stale_and_bundle_metrics_need_no_answer_list() -> None:
    metrics = load_script()
    old = stored("old", minutes=1)
    newer = stored("new", minutes=2).model_copy(
        update={"parent_revision_digest": old.revision_digest}
    )
    bundle = stored("bundle", minutes=3)
    contents: dict[str, Mapping[str, object]] = {
        old.memory_revision_id: {"statement": "표본이 작으면 결론을 보류한다"},
        newer.memory_revision_id: {"statement": "표본이 작으면 결론을 보류한다 다시"},
        bundle.memory_revision_id: BUNDLE,
    }
    svc, store = service(
        [old, newer, bundle],
        {i.owner_revision_ref: contents[i.memory_revision_id] for i in (old, newer, bundle)},
    )
    pack = context(svc)
    result = metrics.compute_metrics(store.contexts, [old, newer, bundle], contents)
    (row,) = result["questions"]
    assert row["counts"]["context_included"] == len(pack.included) == 1
    assert row["counts"]["eligible"] == 1 and row["memory_tokens"] > 0
    assert row["superseded_included"] == 0 and row["container_included"] == 0
    assert row["needed_retrieval_rate"] == {"value": None, "reason": "NEEDS_GOLD_LIST"}
    assert row["used_rate"]["reason"] == "NEEDS_USED_OR_CITED"
    assert result["totals"]["questions"] == 1


def test_with_a_gold_list_recall_and_missing_are_computed_from_the_stages() -> None:
    metrics = load_script()
    items = [stored(f"m{n}", kind=MemoryKind.HYPOTHESIS, minutes=n) for n in range(5)]
    contents: dict[str, Mapping[str, object]] = {
        i.memory_revision_id: {"statement": "표본이 작으면"} for i in items
    }
    svc, store = service(
        items, {i.owner_revision_ref: contents[i.memory_revision_id] for i in items}
    )
    # five memories share the same two words, so a follow-up question is what reaches them
    pack = context(svc, "앞에서 표본이 결론을 어떻게")
    included = [i.memory_revision_id for i in pack.included]
    assert len(included) == 3  # one kind holds at most three
    needed = [included[0], items[0].memory_revision_id, "rev:absent"]
    gold = {pack.query: {"needed_memory_revision_ids": needed}}
    (row,) = metrics.compute_metrics(store.contexts, items, contents, gold)["questions"]
    in_context = len({included[0], items[0].memory_revision_id} & set(included))
    assert row["needed_context_rate"] == {"value": round(in_context / 3, 4)}
    assert row["needed_missing"] == {"value": 3 - in_context}
    assert row["needed_retrieval_rate"]["value"] is not None
    assert pack.selection is not None
    unneeded = len(set(pack.selection.retrieved) - set(needed)) / len(pack.selection.retrieved)
    assert row["unneeded_retrieval_rate"] == {"value": round(unneeded, 4)}


def test_an_off_question_expecting_no_memory_is_a_fit() -> None:
    metrics = load_script()
    items = [stored("m1")]
    svc, store = service(
        items,
        {items[0].owner_revision_ref: {"statement": "표본이 작으면"}},
        injection=__import__(
            "tests.integration.test_memory_selection", fromlist=["Injection"]
        ).Injection(False),
    )
    pack = context(svc)
    gold: dict[str, Mapping[str, object]] = {
        pack.query: {"needed_memory_revision_ids": [], "expects_no_memory": True}
    }
    (row,) = metrics.compute_metrics(store.contexts, items, {}, gold)["questions"]
    assert row["injection_enabled"] is False and row["abstention_fit"] == {"value": True}
