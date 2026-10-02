"""Stream observations remain writable as canonical dispatch journal state."""

from decimal import Decimal

import pytest

from thoth.domain.canonical import canonical_payload
from thoth.domain.model_dispatch import ModelReceiveObservation


@pytest.mark.parametrize("rate", [0, 0.05, 5.25])
def test_stream_rates_are_canonical_and_roundtrip(rate: float) -> None:
    observation = ModelReceiveObservation.model_validate(
        {
            "received_bytes": 0,
            "visible_output_bytes": 0,
            "frame_counts": {},
            "max_stream_bytes": None,
            "max_visible_output_bytes": None,
            "timeout_ms": None,
            "window_events_per_second": rate,
            "stall_limit_events_per_second": rate,
            "dispatch_total_limit_seconds": rate,
        }
    )
    assert observation.window_events_per_second == Decimal(str(rate))
    assert canonical_payload(observation)
    assert ModelReceiveObservation.model_validate_json(observation.model_dump_json()) == observation
    legacy = observation.model_dump(mode="json")
    for field in (
        "window_events_per_second",
        "stall_limit_events_per_second",
        "dispatch_total_limit_seconds",
    ):
        legacy.pop(field)
    assert ModelReceiveObservation.model_validate(legacy).window_events_per_second is None
