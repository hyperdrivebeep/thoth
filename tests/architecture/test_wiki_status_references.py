from __future__ import annotations

from pathlib import Path

from tests.architecture.public_source_profile import current_open_owner_debt, public_source_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT


def assert_profile_status_has_no_stale_claims(workspace: Path) -> None:
    profile = public_source_profile(workspace)
    pages = (
        workspace / "PROJECT_WIKI/80_OPEN_QUESTIONS/INDEX.md",
        workspace / "PROJECT_WIKI/50_SEED_ROADMAP/seed-map.md",
        workspace / "PROJECT_WIKI/50_SEED_ROADMAP/seed-1-memory-kernel.md",
        workspace / "PROJECT_WIKI/40_HACKATHON_DEMO/demo-scope.md",
    )
    stale = (
        "A11 connector allowlist·Project Policy·sandbox policy digest fail-closed enforcement",
        "full FACT/FAILURE/LESSON/PRACTICE/DECISION/PERSON/REFERENCE lifecycle, "
        "dynamic Team Router, candidate/validate/quarantine/retire와 Gate A full contract는 미구현",
        "OpenDreamKit / PENDING MATERIALIZATION",
        "runtime recursive-improvement application",
    )
    if profile is None:
        text = "\n".join(path.read_text(encoding="utf-8") for path in pages)
        assert "ratcheted exception 0" in (workspace / "PROJECT_WIKI/NOW.md").read_text(
            encoding="utf-8"
        )
    else:
        verification = profile.read_text("docs/VERIFICATION.md")
        text = verification
        debt = current_open_owner_debt(profile)
        assert f"ATOMICITY DEBT: {debt} OPEN" in verification
        if debt > 0:
            assert "ratcheted exception 0" not in verification
    assert all(value not in text for value in stale)


def test_status_projection_has_no_stale_implementation_claims() -> None:
    assert_profile_status_has_no_stale_claims(WORKSPACE)
