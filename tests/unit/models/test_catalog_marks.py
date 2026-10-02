"""Fixed and CLI-read lists: labelled as aliases, unchecked, and they remember real outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.catalog_marks import MarkedCatalog
from thoth.adapters.models.catalog_store import CatalogSnapshotStore
from thoth.adapters.models.claude_code import ClaudeCodeCatalog
from thoth.adapters.models.xai_catalog import ThothXaiOAuthCatalog
from thoth.domain.model_settings import ModelOption

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def inner() -> StaticModelCatalog:
    return StaticModelCatalog(
        (
            ModelOption(
                provider="claude-code",
                model="sonnet",
                reasoning_efforts=("low",),
                capability_source="observed",
            ),
        )
    )


def marked(tmp_path: Path) -> MarkedCatalog:
    return MarkedCatalog(
        inner(), provider="claude-code", source="CLI_ALIAS", workspace=tmp_path, now=lambda: NOW
    )


def test_claude_code_options_say_they_are_aliases_and_name_the_cli_version(tmp_path: Path) -> None:
    catalog = ClaudeCodeCatalog(
        tmp_path,
        status=lambda _: {"connected": True, "execution_eligible": True, "cli_version": "2.1.284"},
    )
    labels = {option.model: option.label for option in catalog.options()}
    assert labels == {
        "sonnet": "최신 Sonnet(별칭) · Claude Code 2.1.284",
        "opus": "최신 Opus(별칭) · Claude Code 2.1.284",
        "haiku": "최신 Haiku(별칭) · Claude Code 2.1.284",
    }
    assert all(option.entitlement == "UNVERIFIED" for option in catalog.options())
    unknown = ClaudeCodeCatalog(
        tmp_path, status=lambda _: {"connected": True, "execution_eligible": True}
    )
    assert unknown.options()[0].label == "최신 Sonnet(별칭)"


def test_the_fixed_xai_model_is_listed_as_not_yet_checked_against_the_account(
    tmp_path: Path,
) -> None:
    class Signed:
        def status(self) -> dict[str, object]:
            return {"execution_eligible": True}

    (option,) = ThothXaiOAuthCatalog(tmp_path, broker=Signed()).options()  # type: ignore[arg-type]
    assert option.model == "grok-4.6" and option.entitlement == "UNVERIFIED"
    assert option.execution == "UNVERIFIED"


def test_a_refusal_and_a_success_are_remembered_per_model_without_changing_the_list(
    tmp_path: Path,
) -> None:
    catalog = marked(tmp_path)
    assert catalog.options()[0].execution == "UNVERIFIED"
    catalog.record_execution("claude-code", "sonnet", "REJECTED", "CLAUDE_CODE_MODEL_UNSUPPORTED")
    assert catalog.options()[0].execution == "REJECTED"
    assert [o.model for o in catalog.options()] == ["sonnet"]
    catalog.record_execution("claude-code", "sonnet", "VERIFIED", None)
    assert catalog.options()[0].execution == "VERIFIED"
    again = marked(tmp_path)
    assert again.options()[0].execution == "VERIFIED"
    assert list(CatalogSnapshotStore(tmp_path).root.glob("claude-code-*.json"))


def test_marks_for_another_provider_or_an_unlisted_model_are_ignored(tmp_path: Path) -> None:
    catalog = marked(tmp_path)
    catalog.record_execution("codex-oauth", "sonnet", "REJECTED", "OAUTH_MODEL_NOT_AVAILABLE")
    catalog.record_execution(
        "claude-code", "not-listed", "REJECTED", "CLAUDE_CODE_MODEL_UNSUPPORTED"
    )
    catalog.record_execution("claude-code", "sonnet", "MAYBE", None)
    assert catalog.options()[0].execution == "UNVERIFIED"
    assert not CatalogSnapshotStore(tmp_path).root.exists()


def test_the_status_of_a_fixed_list_is_active_but_never_claims_a_fetch_time(tmp_path: Path) -> None:
    (row,) = marked(tmp_path).statuses()
    assert (row.provider, row.source, row.status, row.fetched_at) == (
        "claude-code",
        "CLI_ALIAS",
        "ACTIVE",
        None,
    )
    empty = MarkedCatalog(
        StaticModelCatalog(), provider="claude-code", source="CLI_ALIAS", workspace=tmp_path
    )
    assert empty.statuses() == ()
