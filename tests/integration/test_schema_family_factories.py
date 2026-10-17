"""103-R: new factories use caller metadata and preserve the frozen schema contract."""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable
from tests.integration.schema_contract_probe import assert_contract_equal, assert_table_identity

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/storage_schema_contract.json"
PROBE = Path(__file__).with_name("schema_contract_probe.py")


FAMILIES = [
    (
        "baseline",
        ("project_head_sets", "baseline_candidates", "baseline_sets", "baseline_decisions"),
    ),
    ("behavior_catalog", ("behavior_artifacts", "behavior_registry")),
    (
        "investigation",
        ("investigations", "investigation_audit", "acquisition_search_intents", "evidence_leads"),
    ),
    (
        "evidence",
        (
            "evidence_sources",
            "evidence_observations",
            "evidence_links",
            "evidence_conflicts",
            "evidence_audit",
            "span_corrections",
        ),
    ),
    (
        "criterion",
        (
            "criterion_profiles",
            "criterion_contracts",
            "criterion_references",
            "criterion_conflicts",
            "criterion_audit",
        ),
    ),
    (
        "decision_object",
        (
            "object_profiles",
            "object_candidates",
            "decision_objects",
            "object_relations",
            "object_attention",
            "object_audit",
        ),
    ),
    (
        "hypothesis",
        (
            "hypothesis_records",
            "hypothesis_portfolios",
            "hypothesis_relations",
            "hypothesis_assumptions",
            "hypothesis_predictions",
            "hypothesis_test_bindings",
            "hypothesis_appraisals",
            "hypothesis_audit",
        ),
    ),
    (
        "action",
        (
            "action_records",
            "action_portfolios",
            "action_plans",
            "action_authorizations",
            "action_audit",
        ),
    ),
    (
        "execution",
        (
            "plan_executions",
            "step_execution_attempts",
            "execution_effects",
            "execution_reconciliations",
            "execution_audit",
        ),
    ),
    (
        "outcome",
        (
            "outcome_profiles",
            "outcome_series",
            "outcome_assessments",
            "outcome_attributions",
            "outcome_change_sets",
            "outcome_impacts",
            "outcome_audit",
        ),
    ),
]


def check_table_bytes(table: Table, expected: dict[str, object]) -> None:
    dialect = sqlite.dialect()
    assert str(CreateTable(table).compile(dialect=dialect)).encode("utf-8") == str(
        expected["sql"]
    ).encode("utf-8")
    assert list(table.c.keys()) == expected["columns"]
    indexes = {
        str(index.name): str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes
    }
    assert indexes == expected["indexes"]
    names = {None if c.name is None else str(c.name) for c in table.constraints}
    assert (
        sorted(names, key=lambda name: (name is not None, name or ""))
        == expected["constraint_names"]
    )
    assert sorted(type(c).__name__ for c in table.constraints) == expected["constraint_types"]


@pytest.mark.parametrize("family,names", FAMILIES)
def test_factory_uses_only_supplied_metadata(family: str, names: tuple[str, ...]) -> None:
    schema = importlib.import_module("thoth.adapters.storage.schema")
    original = dict(schema.metadata.tables)
    module = importlib.import_module("thoth.adapters.storage." + family + "_schema")
    factory = cast(
        Callable[[MetaData], tuple[Table, ...]], getattr(module, "define_" + family + "_tables")
    )
    assert not any(isinstance(value, (MetaData, Table)) for value in vars(module).values())
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_bytes())
    assert all(
        not isinstance(node, (ast.Assign, ast.AnnAssign, ast.ClassDef)) for node in tree.body
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"__future__", "sqlalchemy", "sqlalchemy.types"}
        if isinstance(node, ast.Call):
            assert not (
                isinstance(node.func, ast.Name) and node.func.id in {"MetaData", "create_engine"}
            )
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"][
        "thoth.adapters.storage.schema"
    ]["tables"]
    first: tuple[Table, ...] | None = None
    for _ in range(2):
        supplied = MetaData()
        tables = factory(supplied)
        assert isinstance(tables, tuple) and tuple(t.key for t in tables) == names
        assert list(supplied.tables) == list(names)
        for name, table in zip(names, tables, strict=True):
            assert_table_identity(supplied, table)
            assert getattr(schema, name) is original[name] and table is not original[name]
            check_table_bytes(table, expected[name])
        if first is not None:
            assert all(a is not b for a, b in zip(first, tables, strict=True))
        first = tables
    assert list(schema.metadata.tables) == list(original)
    assert all(schema.metadata.tables[name] is table for name, table in original.items())


@pytest.mark.parametrize("family,names", FAMILIES)
@pytest.mark.parametrize("hash_seed", ["1", "2"])
def test_new_leaf_first_cold_import(
    tmp_path: Path, family: str, names: tuple[str, ...], hash_seed: str
) -> None:
    target = tmp_path / "leaf.json"
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    subprocess.run(
        [sys.executable, str(PROBE), "thoth.adapters.storage." + family + "_schema", str(target)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"][
        "thoth.adapters.storage.schema"
    ]
    actual = json.loads(target.read_text(encoding="utf-8"))["contract"]
    assert_contract_equal(expected, actual)
    assert all(name in actual["root_exports"] for name in names)
