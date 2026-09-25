from thoth.adapters.models.catalog import CompositeModelCatalog, StaticModelCatalog
from thoth.domain.model_settings import ModelOption, ModelSelection


def test_composite_skips_unadvertised_default() -> None:
    broken = StaticModelCatalog(
        (),
        ModelSelection(provider="codex-oauth", model="xai/grok-4.6"),
    )
    xai = StaticModelCatalog(
        (
            ModelOption(
                provider="xai",
                model="grok-4.6",
                reasoning_efforts=("high",),
                default_effort="high",
                capability_source="test",
            ),
        ),
        ModelSelection(provider="xai", model="grok-4.6"),
    )
    defaults = CompositeModelCatalog(broken, xai).defaults()
    assert defaults.provider == "xai"
    assert defaults.model == "grok-4.6"
