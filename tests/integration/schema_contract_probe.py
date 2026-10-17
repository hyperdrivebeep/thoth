"""Cold schema/DDL capture with no database or runtime construction."""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import sqlalchemy
from sqlalchemy import MetaData, Table, create_mock_engine
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

ENTRY_POINTS = (
    "thoth.adapters.storage.schema",
    "thoth.adapters.storage.sqlite",
    "thoth.adapters.storage.research_schema",
    "thoth.adapters.storage.resource_scope_schema",
    "thoth.adapters.storage.behavior_execution_schema",
    "thoth.adapters.storage.evaluation_run_schema",
    "thoth.adapters.storage.test_validity_schema",
    "thoth.adapters.storage.governance_history_schema",
    "thoth.apps.runtime",
)
EXTENSIONS = (
    "behavior_execution_schema",
    "evaluation_run_schema",
    "research_schema",
    "resource_scope_schema",
    "test_validity_schema",
    "governance_history_schema",
)


def assert_table_identity(metadata: MetaData, table: Table) -> None:
    assert table.metadata is metadata, "table has a different MetaData owner"
    assert metadata.tables[table.key] is table, "alias points to a different Table object"


def assert_contract_equal(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    assert actual == expected, "schema contract differs (SQL bytes, identity names or order)"
    # SQL content is compared without strip, whitespace folding or line-ending conversion.
    for name, table in expected["tables"].items():
        assert actual["tables"][name]["sql"].encode("utf-8") == table["sql"].encode("utf-8")
        for index, sql in table["indexes"].items():
            assert actual["tables"][name]["indexes"][index].encode("utf-8") == sql.encode("utf-8")


def capture_contract(entry: str) -> dict[str, Any]:
    assert entry.startswith("thoth.")
    importlib.import_module(entry)
    schema = importlib.import_module("thoth.adapters.storage.schema")
    for module in EXTENSIONS:
        importlib.import_module("thoth.adapters.storage." + module)
    metadata: MetaData = schema.metadata
    exports = {name: obj.key for name, obj in vars(schema).items() if isinstance(obj, Table)}
    assert len(exports) == 97
    aliases: dict[str, str] = {}
    for name, module in sorted(sys.modules.items()):
        if not name.startswith("thoth.adapters.storage."):
            continue
        for attr, obj in vars(module).items():
            if isinstance(obj, Table):
                assert_table_identity(metadata, obj)
                aliases[name + "." + attr] = obj.key
    before_tables = dict(metadata.tables)
    assert importlib.import_module("thoth.adapters.storage.schema") is schema
    importlib.import_module(entry)
    for module in EXTENSIONS:
        importlib.import_module("thoth.adapters.storage." + module)
    assert list(metadata.tables) == list(before_tables)
    assert all(metadata.tables[name] is table for name, table in before_tables.items())

    dialect = sqlite.dialect()
    tables: dict[str, Any] = {}
    for table in metadata.tables.values():
        indexes: dict[str, str] = {}
        for index in table.indexes:
            assert index.name is not None and str(index.name) not in indexes
            indexes[str(index.name)] = str(CreateIndex(index).compile(dialect=dialect))
        names = {None if c.name is None else str(c.name) for c in table.constraints}
        tables[table.key] = {
            "sql": str(CreateTable(table).compile(dialect=dialect)),
            "columns": list(table.c.keys()),
            "indexes": indexes,
            "constraint_names": sorted(names, key=lambda name: (name is not None, name or "")),
            "constraint_types": sorted(type(c).__name__ for c in table.constraints),
        }

    create_table_order: list[str] = []
    emitted_indexes: dict[str, dict[str, str]] = {}

    def emit(statement: Any, *_args: Any, **_kwargs: Any) -> None:
        sql = str(statement.compile(dialect=dialect))
        if isinstance(statement, CreateTable):
            key = statement.element.key
            create_table_order.append(key)
            assert sql == tables[key]["sql"]
        else:
            assert isinstance(statement, CreateIndex), type(statement).__name__
            index = statement.element
            assert index.table is not None and index.name is not None
            rows = emitted_indexes.setdefault(index.table.key, {})
            assert str(index.name) not in rows
            rows[str(index.name)] = sql

    mock = create_mock_engine("sqlite://", emit)
    metadata.create_all(mock, checkfirst=False)
    assert len(create_table_order) == len(metadata.tables)
    for name, info in tables.items():
        assert emitted_indexes.get(name, {}) == info["indexes"]
    return {
        "table_registration_order": list(metadata.tables),
        "sorted_tables": [table.key for table in metadata.sorted_tables],
        "create_table_order": create_table_order,
        "root_export_order": list(exports),
        "root_exports": exports,
        "consumer_aliases": aliases,
        "tables": tables,
    }


def environment() -> dict[str, str]:
    return {
        "python": sys.version,
        "sqlalchemy": sqlalchemy.__version__,
        "sqlite": sqlite3.sqlite_version,
    }


if __name__ == "__main__":
    # This file is executed directly so pytest and its imports cannot warm the child first.
    entry, destination = sys.argv[1:]
    payload = {"environment": environment(), "contract": capture_contract(entry)}
    Path(destination).write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
