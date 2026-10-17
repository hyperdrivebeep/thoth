"""103-V: exact object DDL, import registration order and shared object identity."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable
from tests.integration.schema_contract_probe import (
    ENTRY_POINTS,
    assert_contract_equal,
    assert_table_identity,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/storage_schema_contract.json"
PROBE = Path(__file__).with_name("schema_contract_probe.py")


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize("hash_seed", ["1", "2"])
def test_cold_import_schema_contract(tmp_path: Path, entry: str, hash_seed: str) -> None:
    destination = tmp_path / "captured.json"
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    subprocess.run(
        [sys.executable, str(PROBE), entry, str(destination)],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    actual = json.loads(destination.read_text(encoding="utf-8"))
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"][entry]
    assert_contract_equal(expected, actual["contract"])


@pytest.mark.parametrize("mutation", ["ddl_bytes", "registration_order", "object_identity"])
def test_schema_comparison_negative_controls(mutation: str) -> None:
    expected: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"][
        ENTRY_POINTS[0]
    ]
    assert_contract_equal(expected, copy.deepcopy(expected))
    if mutation == "object_identity":
        owner, other = MetaData(), MetaData()
        table = Table("probe", owner, Column("id", Integer, primary_key=True))
        clone = Table("probe", other, Column("id", Integer, primary_key=True))
        assert str(CreateTable(table).compile(dialect=sqlite.dialect())) == str(
            CreateTable(clone).compile(dialect=sqlite.dialect())
        )
        assert_table_identity(owner, table)
        with pytest.raises(AssertionError, match="different MetaData"):
            assert_table_identity(owner, clone)
        return
    changed = copy.deepcopy(expected)
    if mutation == "ddl_bytes":
        name = next(iter(changed["tables"]))
        changed["tables"][name]["sql"] += " "
    else:
        order = changed["table_registration_order"]
        order[0], order[1] = order[1], order[0]
    with pytest.raises(AssertionError, match="schema contract differs"):
        assert_contract_equal(expected, changed)
