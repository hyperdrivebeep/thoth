"""Keep historical source-context hashes on their original versioned algorithm."""

from typing import cast

from thoth.domain.canonical import canonical_payload, domain_digest


def versioned_source_digest(packet: dict[str, object], version: str) -> str:
    if version not in {"2.0.0", "2.1.0", "2.2.0"}:
        raise ValueError("SOURCE_CONTEXT_VERSION_UNSUPPORTED")
    if version != "2.2.0":
        packet = dict(packet)
        structures = packet.get("structures")
        if isinstance(structures, list):
            packet["structures"] = [
                {
                    k: v
                    for k, v in cast(dict[str, object], row).items()
                    if k != "context_observation_digest"
                }
                if isinstance(row, dict)
                else row
                for row in cast(list[object], structures)
            ]
    return domain_digest("SOURCE_CONTEXT", version, canonical_payload(packet))
