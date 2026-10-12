"""Frozen 0438db8 codec, basis and wire outputs; no Git history is needed to run this test."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from tests.unit.services.test_request_language import model_request
from tests.unit.services.test_research_followup_contract import request_ref

from thoth.adapters.models.codex_oauth import prompt_envelope
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.application.services.research_stages import stage_bases
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.research_codec import decode_research_record


def test_legacy_request_bytes_bases_and_provider_contracts_are_unchanged():
    fixture = json.loads(
        (Path(__file__).parents[2] / "fixtures/legacy_language_contracts.json").read_text(
            encoding="utf-8"
        )
    )
    request = decode_research_record(fixture["request"])
    assert hashlib.sha256(canonical_payload(request)).hexdigest() == fixture["request_sha256"]
    assert "response_language" not in request.model_dump()
    work = SimpleNamespace(request_ref=request_ref(), model_settings=None, response_language=None)
    assert set(fixture["roles"]) == {role.value for role in ModelRole}
    for role in ModelRole:
        model_input = replace(model_request(), role=role)
        expected = fixture["roles"][role.value]
        basis = stage_bases(work, model_input)
        assert (basis.basis, basis.reuse_basis, basis.schema) == (
            expected["basis"],
            expected["reuse_basis"],
            expected["schema"],
        )
        assert (
            hashlib.sha256(prompt_envelope(model_input).encode()).hexdigest()
            == expected["codex_prompt_sha256"]
        )
        assert (
            hashlib.sha256(OpenAIResponsesModel.instructions(model_input).encode()).hexdigest()
            == expected["openai_instructions_sha256"]
        )
