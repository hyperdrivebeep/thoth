from typing import Literal

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel


class WebNavigationHop(DomainModel):
    from_uri: str
    to_uri: str
    kind: Literal["HTTP_REDIRECT", "DOCUMENT_NAVIGATION", "HISTORY_LOCATION"]
    document_sha256: str | None = None
    retrieved_at: AwareDatetime


class WebPage(DomainModel):
    requested_uri: str
    final_uri: str
    media_type: str
    raw: bytes
    retrieved_at: AwareDatetime
    redirects: tuple[str, ...] = ()
    navigation_version: Literal["1.0.0"] = "1.0.0"
    navigation: tuple[WebNavigationHop, ...] = ()
    dom_location_at_capture: str | None = None


class WebTransformation(DomainModel):
    schema_version: Literal["1.0.0", "1.1.0"] = "1.1.0"
    requested_uri: str
    final_uri: str
    http_sha256: str
    http_retrieved_at: AwareDatetime
    rendered_sha256: str
    rendered_at: AwareDatetime
    renderer: str
    redirects: tuple[str, ...] = ()
    navigation: tuple[WebNavigationHop, ...] = ()
    dom_location_at_capture: str | None = None
