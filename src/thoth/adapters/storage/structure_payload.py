"""One structural payload format for both ingestion writers on the same ledger."""

import hashlib

import orjson

from thoth.domain.artifact import StructuralDocument
from thoth.domain.canonical import canonical_payload


def structure_metadata(document: StructuralDocument) -> str:
    payload = document.model_dump(mode="json", exclude={"artifact", "nodes"})
    if not document.document_time_observations:
        payload.pop("document_time_observations", None)
    if document.source_time_assessment is None:
        payload.pop("source_time_assessment", None)
    payload["nodes_digest"] = hashlib.sha256(
        canonical_payload({"nodes": document.nodes})
    ).hexdigest()
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()
