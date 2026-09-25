"""Resolve and version settings before admission; never silently change effort."""

from thoth.application.services.request_records import RequestRecords
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.model_settings import (
    ModelPreferenceRevision,
    ModelSelection,
    ResolvedModelSettings,
)
from thoth.ports.model import ModelResolutionError
from thoth.ports.model_catalog import ModelCatalogPort


class ModelSettingsService:
    def __init__(self, records: RequestRecords, catalog: ModelCatalogPort) -> None:
        self.records, self.catalog = records, catalog

    def preference(
        self, project_id: str, thread_id: str | None
    ) -> tuple[str | None, ModelSelection]:
        record = self.records.read(project_id, EntityType.THREAD, self.key(thread_id))
        if record is None:
            return None, ModelSelection()
        return record[0].revision_digest, ModelPreferenceRevision.model_validate(
            record[1]
        ).selection

    @staticmethod
    def key(thread_id: str | None) -> str:
        return (
            "model-preferences:project"
            if thread_id is None
            else f"model-preferences:thread:{thread_id}"
        )

    def resolve(
        self,
        project_id: str,
        thread_id: str | None,
        selection: ModelSelection,
        *,
        omit_scope: str | None = None,
    ) -> ResolvedModelSettings:
        defaults = self.catalog.defaults()
        provider, model = defaults.provider, defaults.model
        effort = defaults.reasoning_effort
        sources = {
            "provider": "TRANSPORT_DEFAULT",
            "model": "TRANSPORT_DEFAULT",
            "reasoning_effort": "TRANSPORT_DEFAULT",
        }
        levels = [("PROJECT", self.preference(project_id, None)[1])]
        if thread_id is not None:
            levels.append(("THREAD", self.preference(project_id, thread_id)[1]))
        levels.append(("REQUEST", selection))
        for source, layer in levels:
            if source == omit_scope:
                continue
            route_touch = layer.touches_route()
            if route_touch:
                next_provider = layer.resolved_provider()
                next_model = layer.model
                if next_provider is None or next_model is None:
                    raise ModelResolutionError("MODEL_ROUTE_INCOMPLETE")
                changed = (next_provider, next_model) != (provider, model)
                provider, model = next_provider, next_model
                sources["provider"] = source
                sources["model"] = source
                if layer.reasoning_effort is not None:
                    effort = layer.reasoning_effort
                    sources["reasoning_effort"] = source
                elif changed:
                    effort = None
                    sources["reasoning_effort"] = "TRANSPORT_DEFAULT"
            elif layer.reasoning_effort is not None:
                effort = layer.reasoning_effort
                sources["reasoning_effort"] = source
        resolved_provider = provider or "default"
        options = self.catalog.options()
        option = next(
            (o for o in options if o.provider == resolved_provider and o.model == model), None
        )
        if option is None:
            # Legacy injected providers without catalog are allowed only with unchanged defaults.
            if (
                any(s != "TRANSPORT_DEFAULT" for s in sources.values())
                or model is not None
            ):
                raise ModelResolutionError("MODEL_CAPABILITY_UNKNOWN")
            capability_source = "UNREGISTERED_DEFAULT"
        else:
            if effort is not None and effort not in option.reasoning_efforts:
                raise ModelResolutionError("MODEL_REASONING_EFFORT_UNSUPPORTED")
            if (
                effort is None
                and option.default_effort is not None
                and sources["reasoning_effort"] == "TRANSPORT_DEFAULT"
            ):
                effort = option.default_effort
            capability_source = option.capability_source
        payload = {
            "provider": resolved_provider,
            "model": model,
            "reasoning_effort": effort,
            "source_by_field": sources,
            "capability_source": capability_source,
        }
        return ResolvedModelSettings.model_validate(
            {
                **payload,
                "settings_digest": domain_digest(
                    "MODEL_SETTINGS", "1.0.0", canonical_payload(payload)
                ),
            }
        )

    def save(
        self,
        project_id: str,
        thread_id: str | None,
        selection: ModelSelection,
        expected_digest: str | None,
        actor_id: str,
    ) -> str:
        with self.records.ledger.transaction():
            current, _ = self.preference(project_id, thread_id)
            if current != expected_digest:
                raise ModelResolutionError("MODEL_SETTINGS_REVISION_CONFLICT")
            self.resolve(
                project_id,
                thread_id,
                selection,
                omit_scope="PROJECT" if thread_id is None else "THREAD",
            )
            ref = self.records.save(
                project_id,
                EntityType.THREAD,
                self.key(thread_id),
                ModelPreferenceRevision(
                    project_id=project_id, thread_id=thread_id, selection=selection
                ),
                actor_id,
            )
            return ref.revision_digest
