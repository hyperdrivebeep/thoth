from __future__ import annotations

from pathlib import Path


def test_product_core_contains_no_hero_secondary_or_holdout_names() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "thoth"
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in root.rglob("*.py")
    ).casefold()
    for forbidden in (
        "6g-sandbox-hero",
        "sunrise-secondary",
        "opendreamkit-hidden-holdout",
        "public-demo-membrane",
    ):
        assert forbidden not in corpus
