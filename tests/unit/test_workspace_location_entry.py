"""Location policy must run before existing lock/database entry points touch a workspace."""

from pathlib import Path

import pytest

from thoth.adapters.storage import bundle
from thoth.apps import workspace_guard


class LocationDenied(Exception):
    pass


class LockReachedBeforePolicy(Exception):
    pass


@pytest.mark.parametrize("entry", ["store", "server-owner"])
def test_location_policy_precedes_workspace_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    workspace = tmp_path / "blocked"
    trace: list[str] = []
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")

    def deny(*args: object, **kwargs: object) -> None:
        trace.append("location-policy")
        raise LocationDenied("synthetic blocked location")

    def stop_lock(*args: object, **kwargs: object) -> None:
        trace.append("lock")
        raise LockReachedBeforePolicy("lock reached before the location policy")

    target = bundle if entry == "store" else workspace_guard
    monkeypatch.setattr(target, "check_workspace_location", deny, raising=False)
    monkeypatch.setattr(target, "acquire_workspace_owner", stop_lock)
    try:
        with pytest.raises(LocationDenied):
            if entry == "store":
                bundle.SqliteStoreFactory().open(workspace)
            else:
                with workspace_guard.owned_workspace(workspace, command="thoth serve"):
                    pytest.fail("a blocked server reached its body")
        assert trace == ["location-policy"]
    finally:
        assert not workspace.exists()
