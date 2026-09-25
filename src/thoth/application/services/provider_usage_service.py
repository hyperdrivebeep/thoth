"""Persist observed account quota separately from model-call usage."""

from thoth.application.services.request_records import RequestRecords
from thoth.domain.account_usage import AccountQuotaSnapshot
from thoth.domain.enums import EntityType
from thoth.domain.research_request import ThreadRequestRevision
from thoth.ports.provider_usage import ProviderUsagePort


class ProviderUsageService:
    def __init__(self, records: RequestRecords, adapter: ProviderUsagePort | None) -> None:
        self.records = records
        self.adapter = adapter
        self.adapters: dict[str, ProviderUsagePort] = {}

    def read_snapshot(self, project_id: str, thread_id: str) -> AccountQuotaSnapshot:
        provider = self._request_provider(project_id, thread_id)
        stored = self.records.journal_read(
            project_id, self._key(thread_id, provider), AccountQuotaSnapshot
        )
        return stored or AccountQuotaSnapshot()

    def refresh(self, project_id: str, thread_id: str) -> AccountQuotaSnapshot:
        provider = self._request_provider(project_id, thread_id)
        adapter = self.adapters.get(provider or "") or self.adapter
        if adapter is None:
            snapshot = AccountQuotaSnapshot(
                state="UNSUPPORTED", reason="ADAPTER_UNAVAILABLE", provider=provider
            )
        else:
            snapshot = adapter.refresh()
            if snapshot.provider is None and provider is not None:
                snapshot = snapshot.model_copy(update={"provider": provider})
        self.records.journal(
            project_id, self._key(thread_id, snapshot.provider or provider), snapshot
        )
        return snapshot

    def _request_provider(self, project_id: str, thread_id: str) -> str | None:
        record = self.records.read(project_id, EntityType.THREAD, f"request:{thread_id}")
        if record is None:
            return None
        request = ThreadRequestRevision.model_validate(record[1])
        if request.model_settings is None:
            return None
        return request.model_settings.provider

    @staticmethod
    def _key(thread_id: str, provider: str | None) -> str:
        if provider:
            return f"account-quota:{provider}:{thread_id}"
        return f"account-quota:{thread_id}"
