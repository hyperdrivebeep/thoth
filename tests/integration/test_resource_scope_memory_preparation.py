"""Private memories must not influence another actor's review or embedding inputs."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, StaticModelResolver

from thoth.adapters.memory import LocalMemoryEmbedding
from thoth.adapters.storage import SqliteFullMemoryStore
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import FullMemoryRevision, MemoryProjection
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
async def test_private_memories_are_excluded_before_review_and_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            name: ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream=name,
                visibility="WORKSTREAM",
            )
            for name in ("alpha", "beta")
        }
    )
    leaked: list[str] = []
    excerpts: set[str] = set()
    original_conflict = FullProjectMemoryService._has_conflict  # pyright: ignore[reportPrivateUsage]
    original_embed = LocalMemoryEmbedding.embed

    def conflict(
        self: FullProjectMemoryService,
        *,
        revisions: tuple[FullMemoryRevision, ...],
        kind: MemoryKind,
        query_terms: frozenset[str],
        content_excerpt: str,
    ) -> bool:
        actor = current_authenticated_actor()
        if actor is not None and actor.actor_id.endswith(":beta"):
            leaked.extend(
                "review" for r in revisions if r.origin_thread_id == "thread:alpha:private"
            )
        return original_conflict(
            self,
            revisions=revisions,
            kind=kind,
            query_terms=query_terms,
            content_excerpt=content_excerpt,
        )

    def embed(self: LocalMemoryEmbedding, text: str) -> tuple[int, ...]:
        actor = current_authenticated_actor()
        if actor is not None and actor.actor_id.endswith(":beta") and text in excerpts:
            leaked.append("embedding")
        return original_embed(self, text)

    monkeypatch.setattr(FullProjectMemoryService, "_has_conflict", conflict)
    monkeypatch.setattr(LocalMemoryEmbedding, "embed", embed)
    async with scope_harness(
        tmp_path, policy, model_resolver=StaticModelResolver(DynamicA02Model())
    ) as h:
        projections: tuple[MemoryProjection, ...] = ()
        store = SqliteFullMemoryStore(h.runtime.ledger.engine)
        for actor in ("alpha", "beta"):
            value(await h.connect(actor, f"private-{actor}-source", None))
            value(
                await h.call(
                    actor,
                    "thread/start",
                    f"private-{actor}-thread",
                    {
                        "thread_id": f"thread:{actor}:private",
                        "problem": "Which trial method should we compare?",
                        "scope": {"workstream": actor},
                    },
                )
            )
            result = value(
                await h.call(
                    actor,
                    "thread/input",
                    f"private-{actor}-cycle",
                    {
                        "thread_id": f"thread:{actor}:private",
                    },
                )
            )
            if actor == "alpha":
                memories = result["full_project_memory"]["committed"]
                assert memories
                excerpts.update(m["content_excerpt"] for m in memories)
                projections = store.list_projections(h.project)
                assert projections
            else:
                assert result["full_project_memory"]["projection_state"] == "DEFERRED_SCOPE"
                assert result["full_project_memory"]["committed"]
                assert store.list_projections(h.project) == projections
        assert leaked == [], "private memory reached a review or embedding stage"
