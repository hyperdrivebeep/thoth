"""Model-facing instruction text must read as whole sentences.

A rule inserted into the middle of an existing sentence once left the review and
adjudication tasks with orphan fragments ("target, conditions, ..." and
"of question decomposition."). This guards every task string and role contract.
"""

import ast
import re
from pathlib import Path

import pytest

from thoth.adapters.models.codex_oauth import role_contract
from thoth.application.services.research_review_context import research_prompt_version
from thoth.domain.absence_claim import ABSENCE_INSTRUCTION, ABSENCE_RESULT_CONTRACT, AbsenceClaim
from thoth.domain.derived_calculation import SOURCE_ARITHMETIC_CONTRACT
from thoth.domain.enums import ModelRole

SRC = Path(__file__).resolve().parents[2] / "src"
# Sentences may open with an output field name that the contract is describing.
FIELD_OPENERS = {"basis", "proposed_frontier", "table_filler"}


def task_texts() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "task"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        found[f"{path.name}:{value.lineno}"] = value.value
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_TASK" for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                found[f"{path.name}:{node.lineno}"] = node.value.value
    return found


def contract_texts() -> dict[str, str]:
    return {
        "SOURCE_ARITHMETIC": SOURCE_ARITHMETIC_CONTRACT,
        "ABSENCE_INSTRUCTION": ABSENCE_INSTRUCTION,
        "ABSENCE_RESULT_CONTRACT": ABSENCE_RESULT_CONTRACT,
        "ABSENCE_TARGET": AbsenceClaim.model_fields["target"].description or "",
        **{
            role.value: role_contract(role.value, research_prompt_version(role), None)
            for role in ModelRole
        },
    }


def orphan_fragments(text: str) -> list[str]:
    fragments = (part.strip() for part in re.split(r"(?<=\.)\s+", text))
    return [
        part
        for part in fragments
        if part and part[0].islower() and part.split()[0] not in FIELD_OPENERS
    ]


def test_review_tasks_were_found() -> None:
    names = set(task_texts())
    assert any(name.startswith("research_review_context.py:") for name in names)
    assert len(names) >= 8


@pytest.mark.parametrize("source", ["task", "contract"])
def test_no_sentence_starts_mid_clause(source: str) -> None:
    texts = task_texts() if source == "task" else contract_texts()
    broken = {name: orphan_fragments(text) for name, text in texts.items()}
    assert {name: parts for name, parts in broken.items() if parts} == {}


def test_orphan_fragment_is_detected() -> None:
    assert orphan_fragments("Check completeness. of question decomposition. Done.") == [
        "of question decomposition."
    ]


def test_repaired_roles_have_new_prompt_version() -> None:
    assert research_prompt_version(ModelRole.SEMANTIC_REVIEWER) == "semantic_reviewer.v6"
    assert research_prompt_version(ModelRole.REVIEW_ADJUDICATOR) == "review_adjudicator.v6"
    assert research_prompt_version(ModelRole.RESEARCH_PLANNER) == "research_planner.v5"
    assert research_prompt_version(ModelRole.EVIDENCE_RERANKER) == "evidence_reranker.v5"


def test_numeric_instruction_is_in_the_sentence_integrity_inventory() -> None:
    assert contract_texts()["SOURCE_ARITHMETIC"] == (
        "Values calculated from source numbers; "
        "not a causal or computational-complexity determination."
    )
