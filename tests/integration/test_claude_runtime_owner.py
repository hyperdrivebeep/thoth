"""The normal runtime retains the shared Claude auth owner until its final close."""

from pathlib import Path

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.claude_oauth import broker_for_workspace, close_workspace_broker
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.apps.runtime import create_runtime


def test_two_runtimes_release_shared_claude_broker_only_after_last_close(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    resolver = RegisteredModelResolver()
    catalog = StaticModelCatalog()
    first = create_runtime(workspace, model_resolver=resolver, model_catalog=catalog)
    second = create_runtime(workspace, model_resolver=resolver, model_catalog=catalog)
    shared = broker_for_workspace(workspace)
    close_count = 0
    original_close = shared.close

    def observed_close() -> None:
        nonlocal close_count
        close_count += 1
        original_close()

    shared.close = observed_close  # type: ignore[method-assign]
    first_closed = second_closed = False
    try:
        first.close()
        first_closed = True
        assert broker_for_workspace(workspace) is shared
        assert close_count == 0
        second.close()
        second_closed = True
        assert close_count == 1
        fresh = broker_for_workspace(workspace)
        assert fresh is not shared
    finally:
        if not first_closed:
            first.close()
        if not second_closed:
            second.close()
        close_workspace_broker(workspace)
