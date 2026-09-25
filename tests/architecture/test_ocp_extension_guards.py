from __future__ import annotations

import json
from pathlib import Path

from scripts.check_ocp_extensions import (
    connector_route_errors,
    core_closed_errors,
    extension_manifest_errors,
    hero_trace_errors,
    sandbox_runtime_errors,
)

ROOT = Path(__file__).resolve().parents[2]


def test_connector_guard_rejects_kind_and_selector_enumeration(tmp_path: Path) -> None:
    source = tmp_path / "connector_service.py"
    source.write_text(
        "def route(selector):\n"
        "    signatures = (({'bucket', 'key'}, 'S3'),)\n"
        "    return signatures\n",
        encoding="utf-8",
    )

    errors = connector_route_errors(source)

    assert any("OCP-CONNECTOR-001" in error for error in errors)


def test_sandbox_guard_rejects_concrete_class_name_routing(tmp_path: Path) -> None:
    source = tmp_path / "runtime.py"
    source.write_text(
        "def create_runtime(adapter):\n"
        "    return {'DockerSandboxAdapter': 'DOCKER_POC'}.get(type(adapter).__name__)\n",
        encoding="utf-8",
    )

    errors = sandbox_runtime_errors(source)

    assert any("OCP-SANDBOX-001" in error for error in errors)


def test_current_connector_and_sandbox_composition_pass_guards() -> None:
    assert connector_route_errors(
        ROOT / "src/thoth/application/services/connector_service.py"
    ) == []
    assert sandbox_runtime_errors(ROOT / "src/thoth/apps/runtime.py") == []
    assert hero_trace_errors(ROOT / "qa/scenarios/hero_6g_current_core.py") == []


def test_hero_guard_rejects_self_authored_execution_claims(tmp_path: Path) -> None:
    source = tmp_path / "hero.py"
    source.write_text(
        "manifest = {\n"
        "    'normal_entry_method': 'thread/input',\n"
        "    'manual_semantic_rpc_assembly': False,\n"
        "}\n",
        encoding="utf-8",
    )

    errors = hero_trace_errors(source)

    assert any("OCP-HERO-001" in error for error in errors)


def test_extension_manifest_targets_real_registry_symbols() -> None:
    assert extension_manifest_errors(ROOT) == []


def test_factory_guard_rejects_nonexistent_implemented_target(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "src").mkdir()
    manifest = json.loads(
        (ROOT / "config/architecture-conformance.json").read_text(encoding="utf-8")
    )
    for item in manifest["extension_points"]:
        if item["name"] == "CONNECTOR":
            item["factory_target"] = "MissingConnectorRegistryPort"
    (tmp_path / "config/architecture-conformance.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    errors = extension_manifest_errors(tmp_path)

    assert any("OCP-FACTORY-001" in error for error in errors)


def test_application_core_remains_closed_to_concrete_extension_names() -> None:
    assert core_closed_errors(ROOT / "src/thoth/application") == []


def test_core_closed_guard_rejects_concrete_adapter_branch(tmp_path: Path) -> None:
    source = tmp_path / "application"
    source.mkdir()
    (source / "handler.py").write_text(
        "def route(adapter):\n"
        "    if adapter == 'managed-e2b':\n"
        "        return 'E2BManagedSandboxAdapter'\n",
        encoding="utf-8",
    )
    errors = core_closed_errors(source)
    assert any("OCP-CORE-CLOSED-001" in error for error in errors)
