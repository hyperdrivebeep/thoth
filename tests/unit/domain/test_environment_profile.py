from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from thoth.adapters.environment import load_environment_profile
from thoth.domain.environment import EnvironmentProfile


def test_all_committed_environment_profiles_validate() -> None:
    root = Path(__file__).resolve().parents[3] / "config" / "profiles"
    profiles = tuple(load_environment_profile(path) for path in sorted(root.glob("*.yaml")))

    assert len(profiles) == 3
    assert {profile.deployment.value for profile in profiles} == {
        "LOCAL",
        "INTRANET",
        "PRIVATE_CLOUD",
    }
    assert all(not profile.external_writes_enabled for profile in profiles)


def test_intranet_profile_cannot_send_model_context_outside_boundary() -> None:
    with pytest.raises(ValidationError, match="INTRANET profile cannot permit"):
        EnvironmentProfile.model_validate(
            {
                "profile_id": "profile:unsafe",
                "deployment": "INTRANET",
                "bind_host": "0.0.0.0",
                "model_route": "OPENAI_RESPONSES",
                "egress_policy": "ALLOWLIST",
                "allowed_egress_hosts": ["api.openai.com"],
                "connector_allowlist": ["internal-files"],
                "data_residency": "intranet",
                "model_data_may_leave_boundary": True,
                "adapter_available": True,
            }
        )
