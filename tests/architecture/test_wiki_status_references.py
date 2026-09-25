from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT if (REPO_ROOT / "PROJECT_WIKI").is_dir() else REPO_ROOT.parent


def test_post_d4_wiki_pages_have_no_stale_implementation_claims() -> None:
    pages = (
        WORKSPACE / "PROJECT_WIKI/80_OPEN_QUESTIONS/INDEX.md",
        WORKSPACE / "PROJECT_WIKI/50_SEED_ROADMAP/seed-map.md",
        WORKSPACE / "PROJECT_WIKI/50_SEED_ROADMAP/seed-1-memory-kernel.md",
        WORKSPACE / "PROJECT_WIKI/40_HACKATHON_DEMO/demo-scope.md",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in pages)
    stale = (
        "A11 connector allowlist·Project Policy·sandbox policy digest fail-closed enforcement",
        "full FACT/FAILURE/LESSON/PRACTICE/DECISION/PERSON/REFERENCE lifecycle, "
        "dynamic Team Router, candidate/validate/quarantine/retire와 Gate A full contract는 미구현",
        "OpenDreamKit / PENDING MATERIALIZATION",
        "runtime recursive-improvement application",
    )
    assert all(value not in text for value in stale)
    assert "ratcheted exception 0" in (
        WORKSPACE / "PROJECT_WIKI/NOW.md"
    ).read_text(encoding="utf-8")
