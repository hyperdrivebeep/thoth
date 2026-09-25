from typing import Protocol

from thoth.domain.account_usage import AccountQuotaSnapshot


class ProviderUsagePort(Protocol):
    def refresh(self) -> AccountQuotaSnapshot: ...
