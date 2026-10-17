"""Original acquisition result contract, independent of its implementation module."""

import inspect
from dataclasses import MISSING, FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from thoth.application.services.connector_service import ConnectorAcquisition
from thoth.domain.connectors import ConnectorReceipt, ConnectorRunRecord
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.governance import SourceBinding
from thoth.domain.ingestion import IngestionResult


def test_acquisition_fields_signature_and_required_types() -> None:
    assert str(inspect.signature(ConnectorAcquisition)) == (
        "(ingestion: 'IngestionResult', run: 'ConnectorRunRecord', receipt: 'ConnectorReceipt', "
        "binding: 'SourceBinding', source: 'EvidenceSourceRecord') -> None"
    )
    assert [field.name for field in fields(ConnectorAcquisition)] == [
        "ingestion",
        "run",
        "receipt",
        "binding",
        "source",
    ]
    assert all(
        field.default is MISSING and field.default_factory is MISSING
        for field in fields(ConnectorAcquisition)
    )
    assert get_type_hints(ConnectorAcquisition) == {
        "ingestion": IngestionResult,
        "run": ConnectorRunRecord,
        "receipt": ConnectorReceipt,
        "binding": SourceBinding,
        "source": EvidenceSourceRecord,
    }


def test_acquisition_is_frozen_and_keeps_five_object_references() -> None:
    ingestion = IngestionResult.model_construct()
    run = ConnectorRunRecord.model_construct()
    receipt = ConnectorReceipt.model_construct()
    binding = SourceBinding.model_construct()
    source = EvidenceSourceRecord.model_construct()
    result = ConnectorAcquisition(ingestion, run, receipt, binding, source)
    assert result.ingestion is ingestion and result.run is run and result.receipt is receipt
    assert result.binding is binding and result.source is source
    for field in fields(result):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field.name, None)
