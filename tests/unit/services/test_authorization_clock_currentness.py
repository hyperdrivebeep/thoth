"""Approval currentness follows append/lineage order, never wall-clock order."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from tests.unit.services.test_authorization_currentness import NOW, STEP, envelope, plan

from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.schema import action_authorizations
from thoth.application.services.authorization_currentness import approved_authorization
from thoth.domain.action_full import AuthorizationEnvelopeRecord


def approval() -> AuthorizationEnvelopeRecord:
    return envelope(authorization_revision_id="approved", revision_digest="1" * 64)


def consumption(offset: int = -60) -> AuthorizationEnvelopeRecord:
    return envelope(
        authorization_revision_id="consumed",
        revision_digest="2" * 64,
        supersedes_revision_digest="1" * 64,
        state="CONSUMED",
        created_at=NOW + timedelta(seconds=offset),
        consumed_at=NOW + timedelta(seconds=offset),
    )


@pytest.mark.parametrize("reverse", (False, True))
def test_consumed_revision_prevents_reusing_approved_history(reverse: bool) -> None:
    history = (approval(), consumption())
    if reverse:
        history = tuple(reversed(history))
    assert approved_authorization(history, plan("a"), STEP) is None


def test_unique_latest_approved_is_selected_without_using_created_at() -> None:
    first = envelope(authorization_revision_id="pending", revision_digest="0" * 64, state="PENDING")
    latest = approval().model_copy(
        update={
            "supersedes_revision_digest": first.revision_digest,
            "created_at": NOW - timedelta(days=1),
        }
    )
    assert approved_authorization((latest, first), plan("a"), STEP) is latest


def test_another_authorization_can_approve_the_same_step() -> None:
    fresh = envelope(
        authorization_id="auth:2", authorization_revision_id="fresh", revision_digest="3" * 64
    )
    assert approved_authorization((approval(), consumption(), fresh), plan("a"), STEP) is fresh


def test_ambiguous_branch_does_not_select_an_old_or_sibling_approval() -> None:
    sibling = envelope(
        authorization_revision_id="sibling",
        revision_digest="3" * 64,
        supersedes_revision_digest="1" * 64,
    )
    assert approved_authorization((approval(), consumption(), sibling), plan("a"), STEP) is None


@pytest.fixture
def store() -> Iterator[SqliteActionStore]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    action_authorizations.create(engine)
    try:
        yield SqliteActionStore(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("offset", (-60, 0, 60))
def test_store_selects_last_insert_even_on_rollback_or_tied_time(
    store: SqliteActionStore, offset: int
) -> None:
    first, last = approval(), consumption(offset)
    store.add_authorization(first)
    store.add_authorization(last)
    current = store.read_authorization("p", "auth:1")
    assert current is not None and current.revision_digest == last.revision_digest
    assert current.created_at == last.created_at
    assert [row.revision_digest for row in store.list_authorizations("p", "plan:1")] == [
        last.revision_digest
    ]
    fresh = envelope(
        authorization_id="auth:2", authorization_revision_id="fresh", revision_digest="3" * 64
    )
    store.add_authorization(fresh)
    assert [row.authorization_id for row in store.list_authorizations("p", "plan:1")] == [
        "auth:1",
        "auth:2",
    ]


def test_successor_query_is_scoped_and_does_not_depend_on_time(store: SqliteActionStore) -> None:
    first = approval()
    store.add_authorization(first)
    assert not store.has_authorization_successor("p", "auth:1", first.revision_digest)
    store.add_authorization(consumption())
    assert store.has_authorization_successor("p", "auth:1", first.revision_digest)
    assert not store.has_authorization_successor("other-project", "auth:1", first.revision_digest)
    assert not store.has_authorization_successor("p", "other-auth", first.revision_digest)
    assert not store.has_authorization_successor("p", "auth:1", "9" * 64)
    assert not store.has_authorization_successor("p", "auth:1", "2" * 64)
