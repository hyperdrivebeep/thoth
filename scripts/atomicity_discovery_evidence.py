"""Bind reviewed runtime/writer discovery to the current source before accepting new runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def support_sources(root: Path) -> list[dict[str, str]]:
    files = set((root / "src").rglob("*.py")) | set((root / "migrations").rglob("*.py"))
    files.update(
        p
        for p in (root / "config").rglob("*")
        if p.is_file()
        and p.suffix in {".json", ".yaml", ".yml", ".toml"}
        and not p.name.startswith("atomicity-")
        and p.name != "architecture-conformance.json"
    )
    return [{"path": p.relative_to(root).as_posix(), "sha256": sha(p)} for p in sorted(files)]


def discovery_errors(root: Path, inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    binding = inventory.get("discovery_binding", {})
    path = (root / inventory["discovery"]).resolve()
    if not path.is_relative_to(root.resolve()) or binding.get("sha256") != sha(path):
        return ["atomicity reviewed discovery bytes are absent or changed"]
    data = json.loads(path.read_text(encoding="utf-8"))
    if binding.get("support_sources") != support_sources(root):
        errors.append("atomicity discovery supporting source/configuration changed")
    for file in binding.get("scanner_files", []):
        if sha(root / file["path"]) != file["sha256"]:
            errors.append("atomicity discovery scanner changed since review")
    if {f["path"] for f in binding.get("scanner_files", [])} != {
        "scripts/build_atomicity_path_inventory.py",
        "scripts/atomicity_call_graph.py",
    }:
        errors.append("atomicity discovery scanner identity is incomplete")
    current = [
        {"path": p.relative_to(root).as_posix(), "sha256": sha(p)}
        for p in sorted((root / "src/thoth").rglob("*.py"))
        if any(part in {"application", "apps", "adapters", "protocol"} for part in p.parts)
    ]
    if current != data.get("files"):
        errors.append("atomicity discovery is stale for current internal/background writer source")
    if data.get("configuration") != {
        "runtime_bindings": True,
        "call_graph": True,
        "configured": True,
        "projectpack_probe": True,
    } or not data.get("projectpack_probe", {}).get("observed_callable_count"):
        errors.append("atomicity supported-configuration observation is incomplete")
    entries = {row["name"]: row for row in inventory["public_entries"]}
    bindings = data.get("runtime_bindings", [])
    phases = {row["path_id"]: row for row in inventory["phases"]}
    if set(entries) != {row["method"] for row in bindings}:
        errors.append("atomicity observed public bindings differ from inventory")
    for row in bindings:
        entry = entries.get(row["method"], {})
        if (
            entry.get("actual_callable") != row["actual_callable"]
            or entry.get("callable_sha256") != row["callable_sha256"]
        ):
            errors.append(f"atomicity actual binding fingerprint changed: {row['method']}")
        route_writers = {writer["symbol"] for writer in row["call_graph"]["sql_writers"]}
        route_mapped = {
            writer
            for phase_id in entry.get("phase_ids", [])
            for writer in phases.get(phase_id, {}).get("producer_refs", [])
        }
        if not route_writers.issubset(route_mapped):
            errors.append(f"atomicity actual route writer mapping is incomplete: {row['method']}")
    graphs = [r["call_graph"] for r in bindings]
    graphs.append(data.get("projectpack_probe", {}).get("call_graph", {}))
    unresolved = {tuple(row) for graph in graphs for row in graph.get("unresolved_calls", [])}
    reviews = binding.get("unresolved_reviews", [])
    symbols = {
        row["source"].removeprefix("src/").removesuffix(".py").replace("/", ".")
        + "."
        + row["symbol"]: row
        for row in data.get("symbols", [])
    }
    if {tuple(r.get("call", [])) for r in reviews} != unresolved:
        errors.append("atomicity unresolved callable candidates lack an exact reviewed mapping")
    for row in reviews:
        if (
            row.get("resolution") not in {"READ_OR_VALUE", "MAPPED_CALL", "EXTERNAL_IO"}
            or not row.get("reason")
            or not row.get("source_refs")
        ):
            errors.append("atomicity unresolved candidate review is incomplete")
        if row.get("resolution") == "MAPPED_CALL" and not row.get("targets"):
            errors.append("atomicity unresolved writer has no concrete target mapping")
        if any(target not in symbols for target in row.get("targets", [])):
            errors.append("atomicity unresolved candidate target is absent from discovery")
        if any(not (root / ref).is_file() for ref in row.get("source_refs", [])):
            errors.append("atomicity unresolved candidate source reference is absent")
    writers = {r["symbol"] for g in graphs for r in g.get("sql_writers", [])}
    mapped = {s for p in inventory["phases"] for s in p.get("producer_refs", [])}
    mapped.update(s for p in inventory["phases"] for s in p.get("discovery_writer_refs", []))
    if writers - mapped:
        errors.append(
            "atomicity discovered SQL writers are not phase-mapped: "
            + ", ".join(sorted(writers - mapped))
        )
    static_writers = {symbol for symbol, row in symbols.items() if row.get("direct_sql_write")}
    inactive = {
        row["symbol"]
        for row in inventory.get("not_current_runtime_routes", [])
        if row.get("reason") and row.get("caller_review")
    }
    if static_writers - mapped - inactive:
        errors.append(
            "atomicity static SQL writer candidates lack route/disposition review: "
            + ", ".join(sorted(static_writers - mapped - inactive))
        )
    return errors
