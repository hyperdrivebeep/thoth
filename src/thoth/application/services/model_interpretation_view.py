"""Hand a later model role the earlier roles' statements as labelled interpretations.

The stored result keeps its original fields. Only the copy a model receives swaps the bare
confirmed-findings and open-interpretation fields for one versioned view that names the role that
wrote each item and says what was checked about it.
"""

from collections.abc import Mapping
from typing import cast

from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack
from thoth.domain.model_interpretation import ModelInterpretationItem, ModelInterpretationView

REPLACED_KEYS = ("confirmed_findings", "interpretation_open_items")
VIEW_KEY = "model_interpretations"


def _findings(value: object) -> tuple[ModelInterpretationItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("MODEL_INTERPRETATION_FINDINGS_INVALID")
    items: list[ModelInterpretationItem] = []
    for entry in cast(list[object] | tuple[object, ...], value):
        if not isinstance(entry, Mapping):
            raise ValueError("MODEL_INTERPRETATION_FINDINGS_INVALID")
        finding = cast(Mapping[str, object], entry)
        items.append(
            ModelInterpretationItem.model_validate(
                {
                    "producer_role": ModelRole.SEMANTIC_REVIEWER.value,
                    "item_kind": "REVIEWER_FINDING",
                    "check": "CITED_SPAN_IDS_ONLY",
                    "statement": finding.get("statement"),
                    "evidence_refs": finding.get("evidence_refs", ()),
                    "requirement_id": finding.get("requirement_id"),
                    "finding_kind": finding.get("kind"),
                }
            )
        )
    return tuple(items)


def _open_items(value: object) -> tuple[ModelInterpretationItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("MODEL_INTERPRETATION_OPEN_ITEMS_INVALID")
    return tuple(
        ModelInterpretationItem(
            producer_role=ModelRole.RESEARCH_PLANNER.value,
            item_kind="PLANNER_OPEN_ITEM",
            check="NOT_CHECKED",
            statement=item,
        )
        for item in cast(list[object] | tuple[object, ...], value)
        if isinstance(item, str)
    )


def with_model_interpretation_labels(context: ContextPack) -> ContextPack:
    """Return the context with earlier-role statements labelled; unchanged when there are none."""
    research = context.research_context
    items = (
        *_findings(research.get("confirmed_findings")),
        *_open_items(research.get("interpretation_open_items")),
    )
    if not items:
        return context
    kept = {k: v for k, v in research.items() if k not in REPLACED_KEYS}
    view = ModelInterpretationView(items=items).model_dump(mode="json", exclude_none=True)
    return context.model_copy(update={"research_context": {**kept, VIEW_KEY: view}})
