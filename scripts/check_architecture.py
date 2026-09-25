from __future__ import annotations

import json

from architecture_contract import (
    ROOT,
    exceptions_for,
    layer_violations,
    load_manifest,
    pattern_paths,
    rule_bundle_digest,
    validate_rule_documents,
)


def main() -> int:
    manifest = load_manifest()
    errors: list[str] = []
    warnings: list[str] = []

    missing_documents = validate_rule_documents(manifest)
    if missing_documents:
        errors.append(f"missing rule documents: {missing_documents}")

    violations = layer_violations(manifest)
    actual_application = {
        path for path in violations if path.startswith("src/thoth/application/")
    }
    expected_application = set(
        exceptions_for(manifest, "APPLICATION_MUST_NOT_IMPORT_ADAPTERS")
    )
    if actual_application != expected_application:
        errors.append(
            "application adapter-import ratchet changed: "
            f"actual={sorted(actual_application)} expected={sorted(expected_application)}"
        )
    unexpected_other = sorted(set(violations) - actual_application)
    if unexpected_other:
        errors.append(f"unexpected layer violations: {unexpected_other}")

    pattern_rules = {
        "EXTENSION_SELECTION_MUST_USE_FACTORY_REGISTRY": [
            (ROOT / "src/thoth/adapters/connectors", "**/*.py", "if definition.kind =="),
            (ROOT / "src/thoth", "cli.py", 'if profile == "disabled"'),
        ],
        "CORE_MUST_NOT_BRANCH_ON_PROVIDER": [
            (ROOT / "src/thoth/application", "**/*.py", 'request.provider != "codex-oauth"'),
        ],
        "CORE_MUST_NOT_HARDCODE_DOMAIN_PROFILE": [
            (
                ROOT / "src/thoth/application/commands",
                "**/*.py",
                'profile_refs=("SYSTEMS_ENGINEERING_VERIFICATION",)',
            ),
        ],
        "SCHEMA_CHANGES_MUST_USE_MIGRATIONS": [
            (ROOT / "src/thoth", "**/*.py", "metadata.create_all("),
        ],
    }
    for rule, scans in pattern_rules.items():
        actual: set[str] = set()
        for root, glob, pattern in scans:
            actual.update(pattern_paths(root, glob, pattern))
        expected = set(exceptions_for(manifest, rule))
        if actual != expected:
            errors.append(
                f"{rule} ratchet changed: actual={sorted(actual)} expected={sorted(expected)}"
            )

    for item in manifest["known_exceptions"]:
        path = ROOT / str(item["path"])
        if not path.is_file():
            errors.append(f"exception {item['id']} path is missing: {item['path']}")
        pattern = item.get("pattern")
        if pattern is not None and path.is_file() and str(pattern) not in path.read_text(
            encoding="utf-8"
        ):
            errors.append(
                f"exception {item['id']} pattern disappeared; remove/update the exception"
            )

    limit = int(manifest["watch_limits"]["python_module_lines"])
    for path in sorted((ROOT / "src/thoth").rglob("*.py")):
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > limit:
            warnings.append(f"module responsibility watch: {path.relative_to(ROOT)}={count} lines")

    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "rule_bundle_digest": rule_bundle_digest(manifest),
        "ratcheted_exception_count": len(manifest["known_exceptions"]),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
