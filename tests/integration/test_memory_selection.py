"""Recall gives an investigation a few memories, records how it chose, and can be switched off."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_memory_edit import Json, recalled, seed

from thoth.adapters.storage import SqliteFullMemoryStore
from thoth.application.commands.memory_revisions import MemoryRevisionHandlers
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.enums import EntityType, MemoryKind, MemoryPayloadMode
from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
)

CUTOFF = datetime(2026, 9, 1, tzinfo=UTC)
BUNDLE = {
    "portfolio_id": "portfolio:o:1",
    "hypothesis_refs": ["hypothesis:o:a", "hypothesis:o:b"],
}
# Stored the way a record is stored: readable text, not \uXXXX escapes (canonical_payload).
LONG_JSON = json.dumps(
    {"statement": "표본이 작으면 결론을 보류한다", "filler": "가" * 8_000}, ensure_ascii=False
)[:8_000]


class Ledger:
    """Enough of a ledger for recall: current heads, no dependency changes, record contents."""

    def __init__(self, contents: Mapping[str, Mapping[str, object]]) -> None:
        self.contents = contents

    def read_heads(self, project_id: str) -> dict[str, str]:
        return {f"HYPOTHESIS:{digest[:6]}": digest for digest in self.contents}

    def read_dependency_states(self, project_id: str) -> dict[str, object]:
        return {}

    def read_revision_by_digest(self, project_id: str, digest: str) -> Any:
        if digest not in self.contents:
            return None
        return SimpleNamespace(
            project_id=project_id,
            entity_type=EntityType.HYPOTHESIS,
            entity_id=digest[:6],
            revision_digest=digest,
            snapshot_id=f"snapshot:{digest}",
            parent_revision_digests=(),
        )

    def read_snapshot(self, snapshot_id: str) -> Any:
        digest = snapshot_id.removeprefix("snapshot:")
        return SimpleNamespace(content=self.contents[digest])


class Store:
    def __init__(self, items: list[FullMemoryRevision]) -> None:
        self.items, self.contexts = items, []
        self.contexts: list[FullMemoryContextPack] = []

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]:
        return tuple(self.items)

    def put_context(self, context: FullMemoryContextPack) -> None:
        self.contexts.append(context)


class Clock:
    def now(self) -> datetime:
        return CUTOFF


class Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, kind: str) -> str:
        self.count += 1
        return f"{kind}:{self.count}"


class Injection:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def enabled(self, project_id: str) -> bool:
        return self._enabled


def owner(tag: str) -> str:
    return (tag.encode().hex() * 64)[:64]


def stored(
    tag: str,
    *,
    kind: MemoryKind = MemoryKind.HYPOTHESIS,
    excerpt: str | None = None,
    minutes: int = 0,
) -> FullMemoryRevision:
    return FullMemoryRevision(
        memory_revision_id=f"rev:{tag}",
        memory_id=f"memory:{tag}",
        project_id="p",
        origin_thread_id="thread:1",
        payload_mode=MemoryPayloadMode.DOMAIN_REFERENCE,
        kind=kind,
        owner_revision_ref=owner(tag),
        source_ref=f"{kind.value}:{tag}",
        content_excerpt=excerpt or f"{kind.value}:{tag}\n표본이 작으면 결론을 보류한다 {tag}",
        scope={},
        # An automatic memory is only remembered with a source span behind it.
        evidence_refs=("span:1",),
        query_terms=("표본이", "결론을"),
        support_status="SUPPORTED",
        authority_status="AUTHORITATIVE",
        cutoff_at=CUTOFF,
        cutoff_valid=True,
        reviews=tuple(
            MemoryRoleReview(
                role=role, verdict=MemoryReviewVerdict.PASS, reason_code="OK", basis_digest="b" * 64
            )
            for role in MemoryReviewRole
        ),
        transition=MemoryTransition.COMMIT,
        recall_eligible=True,
        action_eligible=False,
        revision_digest=owner("rev-" + tag),
        created_at=CUTOFF + timedelta(minutes=minutes),
    )


def service(
    items: list[FullMemoryRevision],
    contents: Mapping[str, Mapping[str, object]],
    injection: Injection | None = None,
) -> tuple[FullProjectMemoryService, Store]:
    store = Store(items)
    return (
        FullProjectMemoryService(
            store=cast(Any, store),
            candidates=cast(Any, None),
            ledger=cast(Any, Ledger(contents)),
            clock=cast(Any, Clock()),
            ids=cast(Any, Ids()),
            injection=injection,
        ),
        store,
    )


def context(
    svc: FullProjectMemoryService, query: str = "표본이 결론을 어떻게"
) -> FullMemoryContextPack:
    return svc.build_context(
        project_id="p",
        thread_id="thread:q",
        query=query,
        target_use="WORKING_CONTEXT",
        scope={},
        cutoff_at=CUTOFF,
    )


@pytest.mark.asyncio
async def test_a_first_investigation_stores_only_the_records_it_grouped_not_the_bundles(
    tmp_path: Path,
) -> None:
    runtime, project, rows = await seed(tmp_path)
    try:
        assert rows and all(row["transition"] == "COMMIT" for row in rows)
        assert all(row["support_status"] == "SUPPORTED" for row in rows)
        assert not any(str(row["summary"]).endswith(("개 묶음", "개 계획")) for row in rows)
        # A stored body is a line, not the whole record (the old bodies ran to 8,000 characters).
        assert max(len(str(row["content_excerpt"])) for row in rows) < 700
        pack = recalled(runtime, project)
        record = SqliteFullMemoryStore(runtime.ledger.engine).list_contexts(project)[-1]
        assert record.selection is not None and record.selection.context_included
        assert set(record.selection.context_included) == {
            i.memory_revision_id for i in pack.included
        }
    finally:
        runtime.close()


def test_a_bundle_stored_before_bundles_were_left_out_is_not_recalled_and_says_why() -> None:
    bundle, single = stored("bundle"), stored("single", kind=MemoryKind.HYPOTHESIS)
    contents: dict[str, Mapping[str, object]] = {
        bundle.owner_revision_ref: BUNDLE,
        single.owner_revision_ref: {"statement": "표본이 작으면 결론을 보류한다"},
    }
    svc, store = service([bundle, single], contents)
    pack = context(svc)
    assert [i.memory_id for i in pack.included] == ["memory:single"]
    assert pack.excluded_reason_counts == {"CONTAINER_NOT_RECALLED": 1}
    assert pack.selection is not None
    assert pack.selection.excluded["CONTAINER_NOT_RECALLED"] == (bundle.memory_revision_id,)
    assert store.contexts == [pack]

    async def rows() -> dict[str, JsonValue]:
        handlers = MemoryRevisionHandlers(
            full=cast(Any, store),
            ledger=cast(Any, Ledger(contents)),
            owner_state=lambda project_id, digest: "CURRENT",
        )
        return await handlers.revision_list({"project_id": "p"})

    import asyncio

    body = asyncio.run(rows())
    reasons = {
        str(r["memory_id"]): r["not_recalled_because"] for r in cast(list[Json], body["revisions"])
    }
    assert reasons == {"memory:bundle": "CONTAINER_NOT_RECALLED", "memory:single": None}


def test_an_old_eight_thousand_character_body_still_reads_and_is_cut_to_its_share() -> None:
    old = stored("old", excerpt="HYPOTHESIS:old\n" + LONG_JSON)
    assert len(old.content_excerpt) > 7_000
    svc, _ = service([old], {old.owner_revision_ref: {"statement": "표본이 작으면"}})
    pack = context(svc)
    (shown,) = pack.included
    assert len(shown.content_excerpt) <= 640 and shown.content_excerpt == old.content_excerpt[:640]
    assert shown.revision_digest == old.revision_digest
    assert pack.selection is not None and pack.selection.truncated == (old.memory_revision_id,)
    assert old.content_excerpt == "HYPOTHESIS:old\n" + LONG_JSON  # what is stored is untouched


def test_twenty_memories_give_a_bounded_context_and_a_record_of_every_stage() -> None:
    kinds = (MemoryKind.HYPOTHESIS, MemoryKind.ACTION, MemoryKind.FACT)
    items = [stored(f"m{n:02d}", kind=kinds[n % 3], minutes=n) for n in range(20)]
    contents = {i.owner_revision_ref: {"statement": "표본이 작으면"} for i in items}
    svc, _ = service(items, contents)
    # Twenty memories share the same two words, so only a follow-up question reaches them all.
    pack = context(svc, "앞에서 표본이 결론을 어떻게")
    selection = pack.selection
    assert selection is not None
    assert len(pack.included) <= 8 and selection.estimated_tokens <= 1_600
    per_kind: dict[str, int] = {}
    for item in pack.included:
        per_kind[item.kind.value] = per_kind.get(item.kind.value, 0) + 1
    assert max(per_kind.values()) <= 3
    assert len(selection.eligible) == 20
    assert set(selection.context_included) <= set(selection.selected) <= set(selection.retrieved)
    assert set(selection.retrieved) <= set(selection.eligible)
    omitted = pack.excluded_reason_counts["OMITTED_BY_BUDGET"]
    assert omitted == 20 - len(pack.included) == len(selection.excluded["OMITTED_BY_BUDGET"])
    assert sum(selection.omitted_by_limit.values()) == omitted
    assert selection.limits["final"] == 8 and selection.limits["total_tokens"] == 1_600


def test_switched_off_the_investigation_gets_no_memory_and_nothing_stored_changes() -> None:
    items = [stored(f"m{n}") for n in range(3)]
    contents = {i.owner_revision_ref: {"statement": "표본이 작으면"} for i in items}
    svc, store = service(items, contents, Injection(False))
    pack = context(svc)
    assert pack.included == () and pack.injected_into_thread is False
    assert pack.excluded_reason_counts == {"MEMORY_INJECTION_OFF": 3}
    assert pack.selection is not None and pack.selection.injection_enabled is False
    assert store.items == items and store.contexts == [pack]
    on, _ = service(items, contents, Injection(True))
    assert len(context(on).included) == 3


@pytest.mark.asyncio
async def test_the_project_switch_is_read_and_changed_with_the_digest_it_read(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:

        async def call(method: str, key: str, **more: JsonValue) -> Json:
            return value(
                await runtime.bus.dispatch(request(method, key, {"project_id": project, **more}))
            )

        first = await call("memory/settings/read", "ms-read-1")
        # query_expansion joined the settings (memory_query_expansion); its default is on.
        assert first == {"memory_injection": True, "query_expansion": True, "settings_digest": None}
        off = await call(
            "memory/settings/update", "ms-off", memory_injection=False, expected_digest=None
        )
        assert off["memory_injection"] is False and off["settings_digest"] is not None
        stale = await runtime.bus.dispatch(
            request(
                "memory/settings/update",
                "ms-stale",
                {"project_id": project, "memory_injection": True, "expected_digest": None},
            )
        )
        assert (
            stale.error is not None and "MEMORY_SETTINGS_REVISION_CONFLICT" in stale.error.message
        )
        assert (await call("memory/settings/read", "ms-read-2"))["memory_injection"] is False
        again = await call(
            "memory/settings/update",
            "ms-on",
            memory_injection=True,
            expected_digest=cast(str, off["settings_digest"]),
        )
        assert again["memory_injection"] is True
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_follow_up_gets_no_memory_while_the_switch_is_off_and_keeps_it_stored(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "sw-first",
                    {"project_id": project, "thread_id": f"thread:{project}"},
                )
            )
        )
        stored_before = len(listed_memories(runtime, project))
        assert stored_before
        value(
            await runtime.bus.dispatch(
                request(
                    "memory/settings/update",
                    "sw-off",
                    {"project_id": project, "memory_injection": False, "expected_digest": None},
                )
            )
        )
        second = f"thread:{project}:followup"
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "sw-start",
                    {
                        "project_id": project,
                        "thread_id": second,
                        "problem": "Which dataset version lesson should this follow-up reuse?",
                        "scope": {"workstream": "dataset-audit"},
                    },
                )
            )
        )
        followup = value(
            await runtime.bus.dispatch(
                request("thread/input", "sw-input", {"project_id": project, "thread_id": second})
            )
        )
        pack = cast(Json, followup["full_project_memory_context"])
        assert pack["included"] == [] and pack["injected_into_thread"] is False
        assert cast(Json, pack["excluded_reason_counts"]).get("MEMORY_INJECTION_OFF")
        assert len(listed_memories(runtime, project)) >= stored_before
    finally:
        runtime.close()


def listed_memories(runtime: Any, project: str) -> tuple[FullMemoryRevision, ...]:
    return SqliteFullMemoryStore(runtime.ledger.engine).list_revisions(project)


def _correction(
    tag: str, assertion: str = "표본이 작으면 다시 확인한다", minutes: int = 0
) -> FullMemoryRevision:
    return stored(tag, kind=MemoryKind.LESSON, minutes=minutes).model_copy(
        update={
            "payload_mode": MemoryPayloadMode.MEMORY_ASSERTION,
            "source_ref": f"MEMORY:{tag}",
            "assertion": assertion,
            "evidence_refs": (),
        }
    )


def _five(common: str = "프로젝트") -> list[FullMemoryRevision]:
    own = ("사과", "포도", "수박", "참외", "딸기")
    return [
        stored(f"w{n}", minutes=n, excerpt=f"HYPOTHESIS:w{n}\n{own[n]} {common}") for n in range(5)
    ]


def _service_for(items: list[FullMemoryRevision]) -> tuple[FullProjectMemoryService, Store]:
    contents = {i.owner_revision_ref: {"statement": "표본이 작으면"} for i in items}
    return service(items, contents)


def test_an_automatic_memory_that_meets_the_question_on_a_project_wide_word_is_left_out() -> None:
    items = _five()
    svc, _ = _service_for(items)
    pack = context(svc, "프로젝트 일정은 어떻게 되나")
    assert pack.included == ()
    assert pack.excluded_reason_counts == {"AUTO_MEMORY_WEAK_MATCH": 5}
    selection = pack.selection
    assert selection is not None
    assert selection.common_terms == ("프로젝트",) and selection.follow_up is False
    assert set(selection.excluded["AUTO_MEMORY_WEAK_MATCH"]) == {
        i.memory_revision_id for i in items
    }
    # two words of the memory's own bring it in; the project-wide word is not one of them
    both = stored("both", excerpt="HYPOTHESIS:both\n사과 포도 프로젝트")
    svc, _ = _service_for([*items, both])
    pack = context(svc, "프로젝트 사과 포도 는")
    assert [i.memory_id for i in pack.included] == ["memory:both"]


def test_a_users_correction_is_still_recalled_on_one_shared_word() -> None:
    fix = _correction("fix", "프로젝트 표본이 작으면 다시 확인한다")
    svc, _ = _service_for([*_five(), fix])
    pack = context(svc, "프로젝트 일정은 어떻게 되나")
    assert [i.memory_id for i in pack.included] == ["memory:fix"]
    assert pack.excluded_reason_counts["AUTO_MEMORY_WEAK_MATCH"] == 5


def test_a_follow_up_question_recalls_the_latest_memories_without_a_shared_word() -> None:
    fix = _correction("fix", minutes=-30)
    svc, _ = _service_for([*_five(), fix])
    pack = context(svc, "앞에서 세운 가설과 다음 행동을 정리해 줘")
    ids = [i.memory_id for i in pack.included]
    assert ids[0] == "memory:fix" and ids[1] == "memory:w4"
    assert len(ids) == 4 and "QUERY_IRRELEVANT" not in pack.excluded_reason_counts
    selection = pack.selection
    assert selection is not None
    assert selection.follow_up is True and selection.follow_up_markers == ("앞에서",)


def test_an_automatic_memory_stored_without_a_source_is_left_out_and_not_deleted() -> None:
    bare = stored("bare").model_copy(update={"evidence_refs": ()})
    backed = stored("backed")
    contents = {i.owner_revision_ref: {"statement": "표본이 작으면"} for i in (bare, backed)}
    svc, store = service([bare, backed], contents)
    pack = context(svc)
    assert [i.memory_id for i in pack.included] == ["memory:backed"]
    assert pack.excluded_reason_counts == {"AUTO_MEMORY_NO_EVIDENCE": 1}
    assert store.items == [bare, backed]

    async def reason() -> dict[str, object]:
        handlers = MemoryRevisionHandlers(
            full=cast(Any, store),
            ledger=cast(Any, Ledger(contents)),
            owner_state=lambda project_id, digest: "CURRENT",
        )
        body = await handlers.revision_list({"project_id": "p"})
        rows = cast(list[Json], body["revisions"])
        return {str(r["memory_id"]): r["not_recalled_because"] for r in rows}

    import asyncio

    assert asyncio.run(reason()) == {
        "memory:bare": "AUTO_MEMORY_NO_EVIDENCE",
        "memory:backed": None,
    }


@pytest.mark.asyncio
async def test_a_new_automatic_hypothesis_or_action_with_no_source_span_is_held(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from tests.integration.test_a06_full_project_memory import (
        _candidate,  # pyright: ignore[reportPrivateUsage]
    )

    from thoth.adapters.runtime import SystemClock, UuidIdGenerator
    from thoth.adapters.storage import SqliteMemoryStore

    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "m2-seed",
                    {"project_id": project, "thread_id": f"thread:{project}"},
                )
            )
        )
        owners = {
            digest: runtime.ledger.read_revision_by_digest(project, digest)
            for digest in runtime.ledger.read_heads(project).values()
        }
        bare = next(d for d, r in owners.items() if r is not None and not r.evidence_refs)
        svc = FullProjectMemoryService(
            store=SqliteFullMemoryStore(runtime.ledger.engine),
            candidates=SqliteMemoryStore(runtime.ledger.engine),
            ledger=runtime.ledger,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        basis = svc.capture_basis(
            project_id=project, cutoff_at=datetime(2026, 9, 1, tzinfo=UTC), scope={}
        )
        hypothesis = _candidate(
            memory_id="memory:m2:h",
            project_id=project,
            owner_revision_ref=bare,
            source_ref="HYPOTHESIS:hypothesis:o:h",
            kind=MemoryKind.HYPOTHESIS,
        )
        action = hypothesis.model_copy(
            update={
                "memory_id": "memory:m2:a",
                "kind": MemoryKind.ACTION,
                "source_ref": "ACTION:a:1",
            }
        )
        outcome = hypothesis.model_copy(
            update={
                "memory_id": "memory:m2:o",
                "kind": MemoryKind.FACT,
                "source_ref": "OUTCOME:o:1",
            }
        )
        lesson = _candidate(
            memory_id="memory:m2:l",
            project_id=project,
            owner_revision_ref=bare,
            assertion="표본이 작으면 결론을 보류하고 다시 확인한다",
        )
        prepared = await svc.prepare_thread_results(
            basis=basis, thread_id="thread:m2", candidates=(hypothesis, action, outcome, lesson)
        )
        by_id = {r.memory_id: r for r in prepared.revisions}
        for held in ("memory:m2:h", "memory:m2:a"):
            revision = by_id[held]
            assert revision.transition == MemoryTransition.HOLD and not revision.recall_eligible
            assert revision.support_status == "NO_EVIDENCE" and len(revision.reviews) == 4
            facts = next(r for r in revision.reviews if r.role == MemoryReviewRole.FACTS)
            assert facts.verdict == MemoryReviewVerdict.HOLD
            assert facts.reason_code == "AUTO_MEMORY_NO_EVIDENCE"
        # a measured outcome is backed by its execution and a correction by its own words
        assert by_id["memory:m2:o"].transition == MemoryTransition.COMMIT
        assert by_id["memory:m2:l"].transition == MemoryTransition.COMMIT
    finally:
        runtime.close()
