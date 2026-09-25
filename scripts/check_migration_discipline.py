from __future__ import annotations

import json

from alembic.config import Config
from alembic.script import ScriptDirectory
from architecture_contract import ROOT, exceptions_for, load_manifest, pattern_paths


def main() -> int:
    manifest = load_manifest()
    errors: list[str] = []
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        errors.append(f"migration graph must have one head: {heads}")

    migrations = sorted((ROOT / "migrations/versions").glob("*.py"))
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        for token in ("revision", "down_revision", "def upgrade", "def downgrade"):
            if token not in text:
                errors.append(f"migration {path.name} is missing {token}")

    replay_test = ROOT / "tests/integration/test_migrations.py"
    if not replay_test.is_file() or "downgrade" not in replay_test.read_text(encoding="utf-8"):
        errors.append("fresh/downgrade/upgrade migration replay test is missing")

    actual_create_all = pattern_paths(ROOT / "src/thoth", "**/*.py", "metadata.create_all(")
    expected_create_all = set(exceptions_for(manifest, "SCHEMA_CHANGES_MUST_USE_MIGRATIONS"))
    if actual_create_all != expected_create_all:
        errors.append(
            "runtime create_all ratchet changed: "
            f"actual={sorted(actual_create_all)} expected={sorted(expected_create_all)}"
        )

    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "migration_count": len(migrations),
        "heads": heads,
        "runtime_create_all_exceptions": sorted(actual_create_all),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
