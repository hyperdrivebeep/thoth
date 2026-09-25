from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.connectors import (
    ConnectorFactoryRegistry,
    LocalFileConnector,
    load_connector_registry,
)


def test_connector_config_loads_bounded_local_and_git_roots(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    repository_root = tmp_path / "repos"
    inbox.mkdir()
    repository_root.mkdir()
    config = tmp_path / "connectors.yaml"
    config.write_text(
        """
connectors:
  - connector_id: local-approved
    kind: LOCAL
    root: inbox
  - connector_id: git-approved
    kind: GIT
    root: repos
""".strip(),
        encoding="utf-8",
    )

    registry = load_connector_registry(config)

    capabilities = registry.capabilities()
    assert [item.connector_id for item in capabilities] == ["git-approved", "local-approved"]
    assert {item.source_kind for item in capabilities} == {"GIT", "LOCAL"}


def test_connector_config_forbids_inline_dsn_and_insecure_mcp(tmp_path: Path) -> None:
    raw_dsn = tmp_path / "raw-dsn.yaml"
    raw_dsn.write_text(
        """
connectors:
  - connector_id: postgres
    kind: POSTGRES
    dsn: postgresql://user:password@example.invalid/db
    allowed_views: [approved]
""".strip(),
        encoding="utf-8",
    )
    insecure_mcp = tmp_path / "insecure-mcp.yaml"
    insecure_mcp.write_text(
        """
connectors:
  - connector_id: mcp
    kind: MCP
    server_url: http://remote.example.invalid/mcp
    allowed_uri_prefixes: [project://approved/]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_connector_registry(raw_dsn)
    with pytest.raises(ValueError, match="HTTPS or local loopback"):
        load_connector_registry(insecure_mcp)


def test_connector_config_accepts_registered_extension_without_loader_branch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "custom-root"
    root.mkdir()
    config = tmp_path / "custom.yaml"
    config.write_text(
        """
connectors:
  - connector_id: custom-approved
    kind: CUSTOM_FILES
    root: custom-root
""".strip(),
        encoding="utf-8",
    )
    factories = ConnectorFactoryRegistry()
    factories.register(
        "CUSTOM_FILES",
        lambda definition, config_root: LocalFileConnector(
            config_root / str(definition.root),
            connector_id=definition.connector_id,
        ),
    )

    registry = load_connector_registry(config, factory_registry=factories)

    capability = registry.capabilities()[0]
    assert capability.connector_id == "custom-approved"
    assert capability.source_kind == "LOCAL"
