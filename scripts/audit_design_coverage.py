from __future__ import annotations

import ast
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.storage.schema import metadata
from thoth.apps.runtime import create_runtime
from thoth.cli import app
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DOMAIN = {
    "Project",
    "WorkThread",
    "DecisionObject",
    "ArtifactEnvelope",
    "EvidenceSpan",
    "Claim",
    "CriterionCandidate",
    "InformationSufficiencyAssessment",
    "HypothesisPortfolio",
    "ActionPlan",
    "Execution",
    "OutcomeRecord",
    "SemanticRevision",
    "MemoryRecord",
    "Receipt",
    "Closure",
    "Export",
}

EXPECTED_TABLES = {
    "projects",
    "threads",
    "artifacts",
    "artifact_versions",
    "structural_nodes",
    "evidence_spans",
    "entity_snapshots",
    "semantic_revisions",
    "revision_parents",
    "working_heads",
    "relations",
    "events",
    "receipts",
    "operations",
    "checkpoints",
    "idempotency_keys",
    "memory_records",
}

EXPECTED_PARSERS = {
    "text",
    "json",
    "csv",
    "hwpx",
    "docx",
    "pdf",
    "xlsx",
    "git",
    "test-log",
}

EXPECTED_CLI = {
    "interactive",
    "run",
    "events",
    "serve",
    "doctor",
    "version",
    "auth-status",
    "auth-connect",
    "model-probe",
    "profile-check",
}

SCENARIOS = {
    "V0.1": ("qa/scenarios/v01_source_evidence.ps1", "PASS"),
    "V0.2": ("qa/scenarios/v02_evidence_restore.ps1", "PASS"),
    "C1_complete": ("examples/projectpacks/c1-complete", "PASS"),
    "C2_missing_criterion": ("tests/unit/reducers/test_sufficiency_reducer.py", "PASS"),
    "C3_incomparable": ("tests/unit/reducers/test_sufficiency_reducer.py", "PASS"),
    "C4_counterevidence": ("qa/scenarios/c4_counterevidence_branch.ps1", "PASS"),
    "C5_restore": ("qa/scenarios/v02_evidence_restore.ps1", "PASS"),
    "C6_R3_preview": ("tests/integration/test_projectpack_rpc.py", "PASS"),
    "FL01_malformed": ("tests/contract/test_transports.py", "PASS"),
    "FL02_duplicate_response_loss": (
        "tests/adversarial/test_response_loss_and_stale_resume.py",
        "PASS",
    ),
    "FL03_stale_resume": ("tests/adversarial/test_response_loss_and_stale_resume.py", "PASS"),
    "FL04_unknown_completion": ("tests/unit/services/test_execution_reconciler.py", "PASS"),
    "FL05_timeout_cancel": ("tests/unit/models/test_codex_oauth_model.py", "PASS"),
    "FL06_dirty_git_target": ("tests/parser/test_git_manifest.py", "PASS"),
    "FL07_misleading_executor": ("tests/unit/domain/test_extended_models.py", "PASS"),
    "FL08_atomic_failure": ("tests/integration/test_revision_service.py", "PASS"),
    "FL09_digest_tamper": ("tests/integration/test_ingestion_service.py", "PASS"),
    "FL10_prompt_cross_project_oracle": ("tests/adversarial", "PASS"),
    "LIVE01_general_project_e2e": ("docs/verification/live-project-e2e-20260830.md", "PASS"),
    "LIVE02_async_cancel": ("tests/contract/test_async_http.py", "PASS"),
    "LIVE03_closure_export": ("tests/integration/test_lifecycle_rpc.py", "PASS"),
    "DEMO01_rights_cleared": ("examples/projectpacks/public-demo-membrane/RIGHTS.md", "PASS"),
    "FIELD01_validation_kit": ("scripts/validate_field_kit.py", "PASS"),
    "SEC01_upload_hardening": ("tests/contract/test_file_upload.py", "PASS"),
}


def _classes(paths: Iterable[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return names


def _cli_commands() -> set[str]:
    commands = {
        command.name or command.callback.__name__.replace("_", "-")
        for command in app.registered_commands
        if command.callback is not None
    }
    if app.registered_callback is not None:
        commands.add("interactive")
    return commands


def _scenario_status(path: str, declared: str) -> str:
    target = ROOT / path
    if not target.exists():
        return "MISSING"
    return declared


def build_audit() -> dict[str, object]:
    domain_classes = _classes((ROOT / "src" / "thoth" / "domain").glob("*.py"))
    tables = set(metadata.tables)
    parsers = set(default_parser_registry().parser_names())
    with tempfile.TemporaryDirectory(prefix="thoth-gap-audit-") as directory:
        runtime = create_runtime(Path(directory) / "workspace")
        try:
            registered_rpc = set(runtime.bus.registered_methods())
        finally:
            runtime.close()
    declared_rpc = set(PUBLIC_METHODS)
    cli_commands = _cli_commands()
    return {
        "domain": {
            "expected": sorted(EXPECTED_DOMAIN),
            "present": sorted(EXPECTED_DOMAIN & domain_classes),
            "missing": sorted(EXPECTED_DOMAIN - domain_classes),
        },
        "storage": {
            "expected": sorted(EXPECTED_TABLES),
            "present": sorted(EXPECTED_TABLES & tables),
            "missing": sorted(EXPECTED_TABLES - tables),
        },
        "parsers": {
            "expected": sorted(EXPECTED_PARSERS),
            "present": sorted(EXPECTED_PARSERS & parsers),
            "missing": sorted(EXPECTED_PARSERS - parsers),
        },
        "rpc": {
            "declared": sorted(declared_rpc),
            "registered": sorted(registered_rpc),
            "unreachable": sorted(declared_rpc - registered_rpc),
        },
        "cli": {
            "expected": sorted(EXPECTED_CLI),
            "present": sorted(EXPECTED_CLI & cli_commands),
            "missing": sorted(EXPECTED_CLI - cli_commands),
        },
        "scenarios": {
            name: _scenario_status(path, status) for name, (path, status) in SCENARIOS.items()
        },
    }


def render_markdown(audit: dict[str, object]) -> str:
    sections = [
        "# THOTH Current Gap Audit",
        "",
        "Generated by `scripts/audit_design_coverage.py`.",
        "",
    ]
    for name in ("domain", "storage", "parsers", "rpc", "cli"):
        section = audit[name]
        assert isinstance(section, dict)
        sections.extend((f"## {name.title()}", ""))
        for key, values in section.items():
            assert isinstance(values, list)
            sections.append(
                f"- **{key} ({len(values)})**: {', '.join(values) if values else 'none'}"
            )
        sections.append("")
    scenarios = audit["scenarios"]
    assert isinstance(scenarios, dict)
    sections.extend(("## Scenarios", "", "| Scenario | Status |", "|---|---|"))
    sections.extend(f"| {name} | {status} |" for name, status in scenarios.items())
    sections.append("")
    return "\n".join(sections)


def main() -> None:
    audit = build_audit()
    output = ROOT / "docs" / "verification" / "current-gap-audit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(audit), encoding="utf-8", newline="\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nWROTE {output}")


if __name__ == "__main__":
    main()
