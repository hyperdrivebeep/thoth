"""xAI account quota is not observed through Codex app-server. Never invent remaining percent."""

from datetime import UTC, datetime

from thoth.domain.account_usage import AccountQuotaSnapshot


class XaiAccountUsageAdapter:
    def refresh(self) -> AccountQuotaSnapshot:
        return AccountQuotaSnapshot(
            state="UNSUPPORTED",
            observed_at=datetime.now(UTC).isoformat(),
            source="UNAVAILABLE",
            reason="XAI_QUOTA_UNSUPPORTED",
            provider="xai",
        )
