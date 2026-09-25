"""Load the explicit current-environment test policy for all normal pytest entry points."""

from scripts.pytest_environment import (
    pytest_addoption as pytest_addoption,
)
from scripts.pytest_environment import (
    pytest_collection_modifyitems as pytest_collection_modifyitems,
)
