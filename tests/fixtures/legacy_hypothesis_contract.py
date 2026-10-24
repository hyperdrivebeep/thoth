"""Explicit v2/v3/v4 compatibility selection; default-v5 behavior is tested separately."""

from thoth.application.services.hypothesis_contract_v3 import GeneratorContract, generator_contract
from thoth.domain.research_execution import research_work


def legacy_generation_contract() -> GeneratorContract:
    work = research_work.get()
    return generator_contract(
        work is not None and work.hypothesis_contract_v3,
        None if work is None else work.response_language,
    )
