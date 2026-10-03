from pathlib import Path

from tests.architecture.public_source_profile import public_source_profile

ROOT = Path(__file__).resolve().parents[2]


def test_makefile_discovers_pnpm_without_user_specific_path() -> None:
    text = (ROOT / "Makefile.ps1").read_text(encoding="utf-8")
    assert 'Get-Command "pnpm.cmd"' in text
    assert 'Get-Command "pnpm"' in text
    assert "THOTH_PNPM" in text
    assert "C:\\Users\\" not in text


def test_repository_has_profile_appropriate_canonical_contract() -> None:
    profile = public_source_profile(ROOT)
    assert (ROOT / "docs/architecture/rpc-method-catalog.md").is_file()
    if profile is not None:
        profile.read_bytes("docs/architecture/backend-runtime-boundaries.md")
        profile.read_bytes("docs/VERIFICATION.md")
        assert not (ROOT / "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md").exists()
