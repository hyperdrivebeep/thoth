"""New canonical codecs preserve legacy bytes and reject incomplete or mismatched provenance."""

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.hypothesis_grounding_v5 import LEGACY, stage_input, v5_record

from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.hypothesis_v5_projection import (
    HypothesisGenerationDetailsV5,
    PortfolioGenerationDetailsV5,
)
from thoth.domain.research_identity import ResearchFamily, decode_research
from thoth.domain.revision import EntitySnapshot, SemanticRevision


@pytest.mark.parametrize("index", [0, 1, 2])
def test_sealed_pre_change_legacy_record_bytes_and_digest_are_unchanged(index: int) -> None:
    item = json.loads(LEGACY.read_text(encoding="utf-8"))["records"][index]
    original = item["canonical_json"].encode()
    assert hashlib.sha256(original).hexdigest() == item["sha256"]
    record = HypothesisRecord.model_validate_json(original)
    assert canonical_payload(record) == original
    assert record.schema_version == item["version"]
    assert not isinstance(record.generation_details, HypothesisGenerationDetailsV5)


def test_new_record_round_trips_without_promoting_or_losing_origin() -> None:
    record = HypothesisRecord.model_validate(v5_record())
    details = record.generation_details
    assert isinstance(details, HypothesisGenerationDetailsV5)
    assert details.response_language is None
    assert tuple(details.stage_inputs) == ("GENERATOR",)
    assert details.expected_results[0].rows[0].relation_assessment.assessment_status == "UNASSESSED"
    payload = canonical_payload(record)
    assert canonical_payload(HypothesisRecord.model_validate_json(payload)) == payload


def test_new_record_cannot_use_another_projects_generation_origin() -> None:
    with pytest.raises(ValidationError, match="GROUNDING_RECORD_PROJECT_MISMATCH"):
        HypothesisRecord.model_validate({**v5_record(), "project_id": "other"})


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "9.0.0"])
def test_v5_cannot_be_mislabelled_as_another_record_version(version: str) -> None:
    with pytest.raises(ValidationError, match="HYPOTHESIS_RECORD_"):
        HypothesisRecord.model_validate({**v5_record(), "schema_version": version})


@pytest.mark.parametrize("contract", [None, "hypothesis_portfolio.v4", "hypothesis_portfolio.v99"])
def test_13_requires_actual_v5_details(contract: str | None) -> None:
    value = v5_record()
    value["generation_details"]["contract_version"] = contract
    with pytest.raises(ValidationError, match="HYPOTHESIS_RECORD_CODEC_MISMATCH"):
        HypothesisRecord.model_validate(value)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"response_language": "en"}, "GROUNDING_STAGE_LANGUAGE_MISMATCH"),
        ({"prompt_version": "hypothesis_portfolio.v4"}, "GROUNDING_STAGE_CONTRACT_MISMATCH"),
    ],
)
def test_stage_context_cannot_silently_change(change: dict[str, Any], reason: str) -> None:
    value = v5_record()
    source = value["generation_details"]["stage_inputs"]["GENERATOR"]
    source.update(change)
    if change.get("response_language"):
        source["language_policy_version"] = "fixture-policy"
    with pytest.raises(ValidationError, match=reason):
        HypothesisRecord.model_validate(value)


def test_stage_project_and_required_source_are_checked() -> None:
    value = v5_record()
    details = value["generation_details"]
    details["stage_inputs"]["GENERATOR"]["generating_request_ref"]["project_id"] = "other"
    with pytest.raises(ValidationError, match="GROUNDING_STAGE_PROJECT_MISMATCH"):
        HypothesisRecord.model_validate(value)
    value = v5_record()
    details = value["generation_details"]
    details["expected_results"][0]["rows"][0]["source_stage"] = "FILLER"
    with pytest.raises(ValidationError, match="GROUNDING_SOURCE_STAGE_MISSING"):
        HypothesisRecord.model_validate(value)
    details["stage_inputs"]["FILLER"] = stage_input("FILLER")
    HypothesisRecord.model_validate(value)
    del details["stage_inputs"]["GENERATOR"]
    with pytest.raises(ValidationError, match="GROUNDING_GENERATOR_STAGE_MISSING"):
        HypothesisRecord.model_validate(value)


def test_stored_test_targets_and_uniqueness_cannot_drift() -> None:
    value = v5_record()
    groups = value["generation_details"]["expected_results"]
    groups[0]["test_id"] = "unknown"
    with pytest.raises(ValidationError, match="GROUNDING_TEST_NOT_IN_CANDIDATES"):
        HypothesisRecord.model_validate(value)
    groups[0]["test_id"] = "t-h1"
    groups.append(deepcopy(groups[0]))
    with pytest.raises(ValidationError, match="DUPLICATE_TEST_ID"):
        HypothesisRecord.model_validate(value)


def test_portfolio_codec_keeps_fill_version_and_requires_successful_fill_origin() -> None:
    hypothesis = v5_record()
    origin = hypothesis["generation_details"]
    value = {
        "portfolio_revision_id": "r:p",
        "portfolio_id": "portfolio:o",
        "project_id": "p",
        "object_id": "object:o",
        "hypothesis_refs": ["h1"],
        "unknown_reserve": {},
        "revision_digest": "a" * 64,
        "created_at": "2026-10-24T00:00:00Z",
        "schema_version": "1.3.0",
        "generation_details": {
            "candidate_status": "DRAFT",
            "generated_from_head_set": "b" * 64,
            "contract_version": "hypothesis_portfolio.v5",
            "response_language": None,
            "generating_request_ref": origin["generating_request_ref"],
            "stage_inputs": origin["stage_inputs"],
            "table_fill": {"state": "CALLED", "cells": 1, "open_before": 1},
        },
    }
    with pytest.raises(ValidationError, match="GROUNDING_SOURCE_STAGE_MISSING"):
        HypothesisPortfolioRecord.model_validate(value)
    origin["stage_inputs"]["FILLER"] = stage_input("FILLER")
    record = HypothesisPortfolioRecord.model_validate(value)
    raw = canonical_payload(record)
    assert canonical_payload(HypothesisPortfolioRecord.model_validate_json(raw)) == raw
    assert isinstance(record.generation_details, PortfolioGenerationDetailsV5)
    assert record.generation_details.table_fill is not None
    assert record.generation_details.table_fill.contract_version == "hypothesis_table_filler.v6"
    with pytest.raises(ValidationError, match="GROUNDING_RECORD_PROJECT_MISMATCH"):
        HypothesisPortfolioRecord.model_validate({**value, "project_id": "other"})


def test_13_record_is_consumed_by_existing_research_identity_decoder() -> None:
    record = HypothesisRecord.model_validate(v5_record())
    content = record.model_dump(mode="json")
    snapshot = EntitySnapshot(
        snapshot_id="s:h1",
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=record.hypothesis_id,
        schema_version="1.3.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id="r:h1",
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=record.hypothesis_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=(),
        actor=ActorRef(actor_id="actor:test", kind=ActorKind.HUMAN, role="OWNER"),
        reason="synthetic codec probe",
        evidence_refs=(),
        affected_refs=(),
        revision_digest=record.revision_digest,
        created_at=record.created_at,
    )
    decoded = decode_research(revision, snapshot)
    assert decoded.identity.schema_family == ResearchFamily.HYPOTHESIS
    assert isinstance(decoded.record, HypothesisRecord)
    assert canonical_payload(decoded.record) == canonical_payload(record)
