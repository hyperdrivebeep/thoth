import pytest

from thoth.application.services.history_projection import revision_fallback_title
from thoth.domain.enums import EntityType


@pytest.mark.parametrize(
    ("entity_type", "title"),
    [
        (EntityType.HYPOTHESIS, "가설 변경"),
        (EntityType.ACTION, "행동 계획 변경"),
        (EntityType.EVIDENCE, "근거 연결 변경"),
        (EntityType.MEMORY, "기억 변경"),
    ],
)
def test_revision_without_a_restore_profile_is_named_by_what_changed(
    entity_type: EntityType, title: str
) -> None:
    assert revision_fallback_title(entity_type) == title


def test_other_entity_types_keep_the_generic_title() -> None:
    assert revision_fallback_title(EntityType.THREAD) == "연구 항목 변경"
