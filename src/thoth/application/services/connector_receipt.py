"""Build a connector receipt at the original field-evaluation boundary."""

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorCapability,
    ConnectorFetchResult,
    ConnectorReceipt,
)
from thoth.ports.runtime import ClockPort


def build_connector_receipt(
    *,
    request: ConnectorAccessRequest,
    capability: ConnectorCapability,
    run_id: str,
    fetched: ConnectorFetchResult,
    clock: ClockPort,
) -> ConnectorReceipt:
    draft: dict[str, object] = {
        "connector_run_id": run_id,
        "project_id": request.project_id,
        "connector_id": request.connector_id,
        "driver_version": capability.driver_version,
        "policy_digest": request.policy_digest,
        "source_uri": fetched.ref.source_uri,
        "native_version": fetched.ref.native_version.model_dump(mode="json"),
        "content_sha256": fetched.content_sha256,
        "byte_size": len(fetched.raw),
        "recorded_at": clock.now(),
        "semantic_truth_certified": False,
    }
    return ConnectorReceipt.model_validate(
        {
            **draft,
            "receipt_digest": domain_digest(
                "CONNECTOR_RECEIPT",
                "1.0.0",
                canonical_payload(draft),
            ),
        }
    )
