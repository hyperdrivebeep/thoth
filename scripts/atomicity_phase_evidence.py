"""Validate each reviewed publication path against exact passing scenarios and call observations."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any


def phase_errors(
    root: Path, phase: dict[str, Any], passed: set[str], observations: dict[str, Any]
) -> list[str]:
    path_id = phase["path_id"]
    errors: list[str] = []
    verification = phase.get("verification", {})
    if phase.get("blockers"):
        errors.append(f"atomicity phase has unresolved blockers: {path_id}")
    if verification.get("classification") == "READ_ONLY":
        review = verification.get("review", {})
        if (
            phase.get("disposition") != "READ_ONLY_REVIEWED"
            or phase.get("producer_refs")
            or not review.get("reason")
            or not review.get("source_files")
        ):
            errors.append(f"atomicity read-only classification lacks grounded review: {path_id}")
        for row in review.get("source_files", []):
            source = (root / row["path"]).resolve()
            if not source.is_relative_to(root.resolve()) or hashlib.sha256(
                source.read_bytes()
            ).hexdigest() != row.get("sha256"):
                errors.append(f"atomicity read-only review source changed: {path_id}")
        return errors
    if verification.get("classification") != "MUTATION":
        return [*errors, f"atomicity phase has no explicit reviewed classification: {path_id}"]
    cases = verification.get("cases", [])
    if not cases:
        errors.append(f"atomicity phase has no required execution scenarios: {path_id}")
    evidence_refs = phase.get("evidence_refs", [])
    if phase.get("disposition") != "VERIFIED" or evidence_refs != [c.get("nodeid") for c in cases]:
        errors.append(f"atomicity phase execution references are incomplete: {path_id}")
    observed: set[str] = set()
    for case in cases:
        node = case.get("nodeid")
        if not isinstance(node, str) or node not in passed or not case.get("scenario"):
            errors.append(f"atomicity required phase scenario did not pass: {path_id}: {node}")
            continue
        file, function = node.split("::", 1)
        source_path = (root / file).resolve()
        if not source_path.is_relative_to(root.resolve()):
            errors.append(f"atomicity scenario source escapes repository: {path_id}")
            continue
        source = source_path.read_text(encoding="utf-8")
        definition = next(
            (
                n
                for n in ast.walk(ast.parse(source))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == function.split("[")[0]
            ),
            None,
        )
        excerpt = "" if definition is None else ast.get_source_segment(source, definition) or ""
        if (
            not excerpt
            or case.get("test_source_sha256") != hashlib.sha256(excerpt.encode()).hexdigest()
        ):
            errors.append(
                f"atomicity phase state-assertion scenario source changed: {path_id}: {node}"
            )
        actual = observations.get(node)
        if not isinstance(actual, list) or not all(isinstance(s, str) for s in actual):
            errors.append(
                f"atomicity phase scenario has no callable observations: {path_id}: {node}"
            )
            continue
        expected = case.get("observed_callables")
        if not isinstance(expected, list) or not expected or not set(expected).issubset(actual):
            errors.append(f"atomicity required callable was not observed: {path_id}: {node}")
        observed.update(actual)
    required = verification.get("required_callables", [])
    if (
        not required
        or phase["actual_callable"] not in required
        or not set(phase.get("producer_refs", [])).issubset(required)
        or not set(required).issubset(observed)
    ):
        errors.append(f"atomicity mandatory entry/writer coverage is incomplete: {path_id}")
    return errors
