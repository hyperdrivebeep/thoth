import json
from pathlib import Path

import pytest
from scripts.pytest_environment import MANIFEST, inactive_hook_nodeids


def configure(root: Path, hooks: object, nodeids: list[str]) -> None:
    (root / "config").mkdir()
    (root / ".codex").mkdir()
    (root / MANIFEST).write_text(
        json.dumps(
            {"schema_version": "1.0.0", "hook_config": ".codex/hooks.json", "nodeids": nodeids}
        ),
        encoding="utf-8",
    )
    (root / ".codex/hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def test_disabled_hooks_exclude_only_explicit_development_cases(tmp_path: Path) -> None:
    node = "tests/architecture/test_legacy.py::test_hook"
    configure(tmp_path, {}, [node])
    assert inactive_hook_nodeids(tmp_path) == {node}
    assert (
        "tests/architecture/test_public_protocol_exact_parity.py::test_parity"
        not in inactive_hook_nodeids(tmp_path)
    )


def test_enabled_hooks_restore_full_diagnostic_selection(tmp_path: Path) -> None:
    configure(
        tmp_path,
        {"PreToolUse": [{"command": "hook"}]},
        ["tests/architecture/test_legacy.py::test_hook"],
    )
    assert not inactive_hook_nodeids(tmp_path)


@pytest.mark.parametrize(
    "nodes",
    [
        ["tests/integration/test_runtime.py::test_product"],
        ["tests/architecture/test_one.py::*", "tests/architecture/test_one.py::*"],
        ["tests/architecture/../integration/test_one.py::test_product"],
    ],
)
def test_invalid_or_product_exclusions_are_rejected(tmp_path: Path, nodes: list[str]) -> None:
    configure(tmp_path, {}, nodes)
    with pytest.raises(ValueError, match="INVALID_HOOK_TEST_SCOPE_NODEIDS"):
        inactive_hook_nodeids(tmp_path)


def test_unknown_hook_configuration_does_not_authorize_exclusions(tmp_path: Path) -> None:
    configure(tmp_path, None, ["tests/architecture/test_legacy.py::test_hook"])
    with pytest.raises(ValueError, match="HOOK_CONFIGURATION_UNAVAILABLE"):
        inactive_hook_nodeids(tmp_path)
