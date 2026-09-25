from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel

InternetConsent = Literal["UNDECIDED", "DENIED", "ALLOWED"]


class WorkspaceSetupState(DomainModel):
    schema_version: int = 1
    revision: int = Field(ge=1, default=1)
    internet_consent: InternetConsent = "UNDECIDED"
    internet_grant_id: str | None = None


class PublicWebPolicy(DomainModel):
    enabled: bool = False
    preferred_hosts: tuple[str, ...] = ()
    workspace_grant_id: str | None = None
