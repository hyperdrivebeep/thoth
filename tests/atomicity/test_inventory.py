import inspect
import json
from pathlib import Path

from thoth.apps.runtime import create_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_covers_actual_queries_commands_and_alias_bindings(tmp_path: Path) -> None:
    catalog = json.loads((ROOT / "schemas/protocol/public-method-catalog.json").read_text())
    inventory = json.loads((ROOT / "config/atomicity-paths.json").read_text())
    expected = {row["name"]: row for row in inventory["public_entries"]}
    assert len(expected) == len(inventory["public_entries"])
    assert set(expected) == {row["name"] for row in catalog["methods"]}
    runtime = create_runtime(tmp_path)
    try:
        registry = runtime.bus._registry  # pyright: ignore[reportPrivateUsage]
        assert set(expected) == set(registry.registered_methods())
        for method in registry.registered_methods():
            handler = inspect.unwrap(registry.resolve(method))
            assert expected[method]["actual_callable"] == (
                handler.__module__ + "." + handler.__qualname__
            )
            if not expected[method]["canonical"]:
                assert expected[method]["alias_target"] in expected
    finally:
        runtime.close()
