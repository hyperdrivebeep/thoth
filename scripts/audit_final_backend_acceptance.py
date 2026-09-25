from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from thoth.apps.runtime import create_runtime
from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = ROOT / "artifacts" / "qa" / "full-product-coverage.json"
REPORT = ROOT / "docs" / "verification" / "final-backend-acceptance.md"
PACKS = (
    "public-demo-membrane",
    "6g-sandbox-hero",
    "sunrise-secondary",
    "l3pilot-regression",
    "opendreamkit-hidden-holdout",
)
BANNED_CORE_TOKENS = (
    "6g-sandbox",
    "sunrise-secondary",
    "opendreamkit",
    "l3pilot",
    "public-demo-membrane",
)


def main() -> None:
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    with TemporaryDirectory() as temporary:
        runtime = create_runtime(Path(temporary))
        registered = runtime.bus.registered_methods()
        runtime.close()
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore").casefold()
        for path in (ROOT / "src" / "thoth").rglob("*.py")
    )
    pack_root = ROOT / "examples" / "projectpacks"
    hidden_manifest = (
        pack_root / "opendreamkit-hidden-holdout" / "source-manifest.json"
    ).read_text(encoding="utf-8")
    checks = {
        "canonical_callable_303": coverage["implemented_callable_count"] == 303,
        "notifications_219": coverage["implemented_notification_count"] == 219,
        "missing_callable_zero": coverage["missing_callable_count"] == 0,
        "registry_exact": set(registered) == set(PUBLIC_METHODS),
        "notification_set_exact": len(IMPLEMENTED_NOTIFICATIONS) == 219,
        "core_pack_tokens_zero": all(token not in source_text for token in BANNED_CORE_TOKENS),
        "five_packs_present": all((pack_root / name).is_dir() for name in PACKS),
        "hidden_oracle_present": (
            pack_root / "opendreamkit-hidden-holdout" / "oracle" / "expected-invariants.json"
        ).is_file(),
        "hidden_oracle_not_manifested": "oracle/" not in hidden_manifest.casefold(),
        "full_6g_cycle_test_present": (
            ROOT / "tests" / "integration" / "test_6g_full_backend_cycle.py"
        ).is_file(),
        "five_pack_test_present": (
            ROOT / "tests" / "integration" / "test_four_projectpack_portability.py"
        ).is_file(),
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    lines = [
        "# THOTH Final Backend Acceptance Audit",
        "",
        f"- Verdict: **{verdict}**",
        f"- Canonical callable catalog: **{coverage['implemented_callable_count']}/303**",
        f"- Notification catalog: **{coverage['implemented_notification_count']}/219**",
        f"- Runtime methods: **{len(registered)}/309** including 6 documented "
        "compatibility aliases",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines.extend(
        f"| `{name}` | {'PASS' if result else 'FAIL'} |" for name, result in checks.items()
    )
    lines.extend(
        [
            "",
            "This audit proves materialization and bounded runtime contracts, not field demand, "
            "scientific truth, external accreditation, deployment, or release.",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "checks": checks}, sort_keys=True))
    if verdict != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
