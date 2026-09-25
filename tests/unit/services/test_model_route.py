from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.application.services.model_settings import ModelSettingsService
from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelResolutionError


class _EmptyRecords(RequestRecords):
    def __init__(self) -> None:
        pass

    def read(
        self, project_id: str, entity_type: EntityType, entity_id: str
    ) -> tuple[RevisionRef, dict[str, object]] | None:
        return None


def _service() -> ModelSettingsService:
    return ModelSettingsService(
        _EmptyRecords(),
        StaticModelCatalog(
            (
                ModelOption(
                    provider="codex-oauth",
                    model="gpt-5.6-terra",
                    reasoning_efforts=("low", "medium", "high", "xhigh"),
                    default_effort="medium",
                    capability_source="codex",
                ),
                ModelOption(
                    provider="xai",
                    model="grok-4.6",
                    reasoning_efforts=("low", "medium", "high", "xhigh"),
                    default_effort="high",
                    capability_source="xai",
                ),
            ),
            ModelSelection(
                provider="codex-oauth", model="gpt-5.6-terra", reasoning_effort="xhigh"
            ),
        ),
    )


def test_route_replace_drops_previous_effort() -> None:
    resolved = _service().resolve(
        "project:1", None, ModelSelection(provider="xai", model="grok-4.6")
    )
    assert (resolved.provider, resolved.model, resolved.reasoning_effort) == (
        "xai",
        "grok-4.6",
        "high",
    )
    assert resolved.source_by_field["provider"] == "REQUEST"
    assert resolved.source_by_field["model"] == "REQUEST"


def test_incomplete_route_is_rejected() -> None:
    try:
        _service().resolve("project:1", None, ModelSelection(provider="xai"))
    except ModelResolutionError as exc:
        assert str(exc) == "MODEL_ROUTE_INCOMPLETE"
    else:
        raise AssertionError("expected MODEL_ROUTE_INCOMPLETE")


def test_effort_only_keeps_current_route() -> None:
    resolved = _service().resolve(
        "project:1", None, ModelSelection(reasoning_effort="low")
    )
    assert (resolved.provider, resolved.model, resolved.reasoning_effort) == (
        "codex-oauth",
        "gpt-5.6-terra",
        "low",
    )
