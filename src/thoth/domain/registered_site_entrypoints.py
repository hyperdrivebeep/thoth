from __future__ import annotations

from urllib.parse import urlsplit

from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID

_ARXIV_RECENT = {
    "arxiv:cs.LG:recent": "https://arxiv.org/list/cs.LG/recent",
    "arxiv:cs.CL:recent": "https://arxiv.org/list/cs.CL/recent",
    "arxiv:cs.CV:recent": "https://arxiv.org/list/cs.CV/recent",
    "arxiv:stat.ML:recent": "https://arxiv.org/list/stat.ML/recent",
}


def registered_entrypoints(hosts: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    allowed = {host.lower() for host in hosts}
    entries: list[dict[str, str]] = []
    if "arxiv.org" in allowed:
        for entrypoint_id, uri in _ARXIV_RECENT.items():
            entries.append(
                {
                    "mode": "SITE_DISCOVER",
                    "entrypoint_id": entrypoint_id,
                    "uri": uri,
                    "connector_id": PROJECT_PUBLIC_WEB_CONNECTOR_ID,
                }
            )
    return tuple(entries)


def entrypoint_uri(entrypoint_id: str, hosts: tuple[str, ...]) -> str:
    allowed = {host.lower() for host in hosts}
    uri = _ARXIV_RECENT.get(entrypoint_id)
    if uri is None:
        raise KeyError(entrypoint_id)
    host = (urlsplit(uri).hostname or "").lower()
    if host not in allowed:
        raise KeyError(entrypoint_id)
    return uri
