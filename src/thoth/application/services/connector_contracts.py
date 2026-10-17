"""Immutable connector acquisition result shared by application consumers."""

from __future__ import annotations

from dataclasses import dataclass

from thoth.domain.connectors import ConnectorReceipt, ConnectorRunRecord
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.governance import SourceBinding
from thoth.domain.ingestion import IngestionResult


@dataclass(frozen=True)
class ConnectorAcquisition:
    ingestion: IngestionResult
    run: ConnectorRunRecord
    receipt: ConnectorReceipt
    binding: SourceBinding
    source: EvidenceSourceRecord
