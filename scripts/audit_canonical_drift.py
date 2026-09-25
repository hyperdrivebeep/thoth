from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT if (ROOT / "PROJECT_WIKI").is_dir() else ROOT.parent


def contains(path: str, text: str) -> bool:
    return text in (WORKSPACE / path).read_text(encoding="utf-8")


def main() -> None:
    coverage = json.loads(
        (ROOT / "artifacts" / "qa" / "full-product-coverage.json").read_text(
            encoding="utf-8"
        )
    )
    checks = {
        "home_current_truth_active": contains(
            "PROJECT_WIKI/HOME.md",
            "CONTRACT SURFACE PASS / LOCAL CORE PARTIAL",
        ),
        "canonical_6g_hero": contains(
            "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md",
            "Engineering Hero: 6G-SANDBOX",
        ),
        "canonical_open_dream_kit_holdout": contains(
            "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md",
            "Hidden Holdout: OpenDreamKit",
        ),
        "membrane_not_hidden": contains(
            "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md",
            "Membrane fixture는 현재 공개 runbook에 노출됐으므로 hidden holdout 자격을 상실",
        ),
        "e05_locked": contains(
            "research-briefs/OPEN_DECISIONS_MASTER_CHECKLIST.md",
            "| E05 | Hero·secondary·hidden holdout 최종 프로젝트 선정 | LOCKED |",
        ),
        "research_index_6g": contains(
            "PROJECT_WIKI/70_RESEARCH/INDEX.md", "Executable Hero Materialization"
        ),
        "full_backend_dag": (
            WORKSPACE
            / "research-briefs"
            / "THOTH_FULL_BACKEND_MATERIALIZATION_DAG.md"
        ).is_file(),
        "operator_tui_gap_recorded": contains(
            "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md",
            "Operator-grade human TUI | D0",
        ),
        "coverage_progress_recorded": (
            int(coverage["designed_callable_count"]) == 303
            and int(coverage["designed_notification_count"]) == 219
            and int(coverage["implemented_callable_count"]) == 303
            and int(coverage["implemented_notification_count"]) == 219
        ),
    }
    payload = {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
    }
    print(json.dumps(payload))
    if payload["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
