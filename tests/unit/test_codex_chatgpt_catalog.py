import json
from pathlib import Path

from thoth.adapters.models.catalog import CodexModelCatalog, chatgpt_codex_model_id


def test_chatgpt_codex_skips_router_aliases_and_unsupported_slugs(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('model = "gpt-5.4-mini"\n', encoding="utf-8")
    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-5.4-mini",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "medium"}],
                        "default_reasoning_level": "medium",
                    },
                    {
                        "slug": "cursor/gpt-5.5",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "medium"}],
                        "default_reasoning_level": "medium",
                    },
                    {
                        "slug": "gpt-5.5",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                        "default_reasoning_level": "medium",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    catalog = CodexModelCatalog(tmp_path)
    assert chatgpt_codex_model_id("gpt-5.4-mini") is None
    assert catalog.defaults().model is None
    assert [option.model for option in catalog.options()] == ["gpt-5.5"]
