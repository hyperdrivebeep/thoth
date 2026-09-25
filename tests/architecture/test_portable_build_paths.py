from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_makefile_discovers_pnpm_without_user_specific_path() -> None:
    text = (ROOT / "Makefile.ps1").read_text(encoding="utf-8")
    assert "Get-Command \"pnpm.cmd\"" in text
    assert "Get-Command \"pnpm\"" in text
    assert "THOTH_PNPM" in text
    assert "C:\\Users\\" not in text


def test_repository_contains_internal_canonical_truth() -> None:
    assert (ROOT / "PROJECT_WIKI/NOW.md").is_file()
    assert (ROOT / "PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md").is_file()
    assert (ROOT / "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md").is_file()
