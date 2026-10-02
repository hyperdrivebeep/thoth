"""The last good model list is kept in the workspace and never replaced by a bad candidate."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.models.catalog_store import CatalogSnapshotStore, CatalogStoreError
from thoth.domain.model_catalog import CatalogSnapshot, authority_digest
from thoth.domain.model_settings import ModelOption

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ACCOUNT = "account:secret-id-42"
DIGEST = authority_digest("codex-oauth", ACCOUNT)


def snapshot(model: str = "gpt-5.5", digest: str = DIGEST) -> CatalogSnapshot:
    return CatalogSnapshot(
        provider="codex-oauth",
        source="PROVIDER_LIST",
        authority_digest=digest,
        fetched_at=NOW,
        validated_at=NOW,
        options=(
            ModelOption(
                provider="codex-oauth",
                model=model,
                reasoning_efforts=("low", "high"),
                capability_source="codex-app-server/model-list-pinned-v1",
                entitlement="PROVIDER_LISTED",
            ),
        ),
    )


def test_a_saved_snapshot_is_read_back_and_holds_no_account_identity(tmp_path: Path) -> None:
    store = CatalogSnapshotStore(tmp_path)
    store.save(snapshot())
    assert store.load("codex-oauth", DIGEST) == snapshot()
    (path,) = store.root.glob("*.json")
    assert path.name == f"codex-oauth-{DIGEST}.json"
    text = path.read_text(encoding="utf-8")
    assert ACCOUNT not in text and "token" not in text.lower()
    assert json.loads(text)["schema_version"] == "1.0.0"
    assert sorted(item.name for item in store.root.iterdir()) == [path.name]


def test_another_account_never_reads_a_previous_accounts_list(tmp_path: Path) -> None:
    store = CatalogSnapshotStore(tmp_path)
    store.save(snapshot())
    other = authority_digest("codex-oauth", "account:someone-else")
    assert store.load("codex-oauth", other) is None
    assert store.load("claude-code", DIGEST) is None


def test_a_failed_write_leaves_the_active_list_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CatalogSnapshotStore(tmp_path)
    store.save(snapshot("gpt-5.5"))

    def broken(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(CatalogStoreError):
        store.save(snapshot("gpt-6"))
    monkeypatch.undo()
    loaded = store.load("codex-oauth", DIGEST)
    assert loaded is not None and loaded.options[0].model == "gpt-5.5"
    assert [item.suffix for item in store.root.iterdir()] == [".json"]


def test_a_candidate_that_does_not_read_back_the_same_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CatalogSnapshotStore(tmp_path)
    store.save(snapshot("gpt-5.5"))
    real = Path.write_text

    def truncating(self: Path, data: str, *args: object, **kwargs: object) -> int:
        return real(self, data[: len(data) // 2], *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", truncating)
    with pytest.raises(CatalogStoreError):
        store.save(snapshot("gpt-6"))
    monkeypatch.undo()
    loaded = store.load("codex-oauth", DIGEST)
    assert loaded is not None and loaded.options[0].model == "gpt-5.5"


def test_a_damaged_saved_list_is_ignored_and_kept_aside(tmp_path: Path) -> None:
    store = CatalogSnapshotStore(tmp_path)
    store.save(snapshot())
    (path,) = store.root.glob("*.json")
    path.write_text("{ not json", encoding="utf-8")
    assert store.load("codex-oauth", DIGEST) is None
    assert not path.exists()
    (backup,) = store.root.glob("*.corrupt*")
    assert backup.read_text(encoding="utf-8") == "{ not json"
    store.save(snapshot("gpt-6"))
    loaded = store.load("codex-oauth", DIGEST)
    assert loaded is not None and loaded.options[0].model == "gpt-6"


def test_a_list_saved_under_another_accounts_name_is_damaged_not_used(tmp_path: Path) -> None:
    store = CatalogSnapshotStore(tmp_path)
    other = authority_digest("codex-oauth", "account:someone-else")
    store.save(snapshot(digest=other))
    source = store.root / f"codex-oauth-{other}.json"
    source.replace(store.root / f"codex-oauth-{DIGEST}.json")
    assert store.load("codex-oauth", DIGEST) is None
