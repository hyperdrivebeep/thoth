from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from thoth.domain.environment import EnvironmentProfile


def load_environment_profile(path: Path) -> EnvironmentProfile:
    loaded = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise ValueError("environment profile must be a YAML object")
    mapping = cast(dict[object, object], loaded)
    return EnvironmentProfile.model_validate({str(key): value for key, value in mapping.items()})
