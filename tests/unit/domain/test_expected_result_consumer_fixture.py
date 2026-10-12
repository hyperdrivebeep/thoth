import json
from pathlib import Path

from scripts.hypothesis_v3_metrics import _unknown

from thoth.domain.hypothesis import ExpectedResult
from thoth.domain.hypothesis_v4 import ExpectedResultV4, expected_is_unknown


def test_backend_and_metrics_retain_each_legacy_classification_and_use_typed_state():
    fixture = Path(__file__).parents[2] / "fixtures/language_expected_results.json"
    for case in json.loads(fixture.read_text(encoding="utf-8")):
        raw = case["row"]
        codec = ExpectedResultV4 if "knowledge_state" in raw else ExpectedResult
        row = codec.model_validate(raw)
        assert expected_is_unknown(row) is case["server"], case["name"]
        assert _unknown(raw) is case["server"], case["name"]
        assert json.loads(row.model_dump_json()) == raw
