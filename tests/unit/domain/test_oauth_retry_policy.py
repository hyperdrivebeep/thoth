from thoth.domain.oauth_retry import allows_once_transient_429


def test_allows_once_transient_429_for_codex_capability() -> None:
    assert allows_once_transient_429(
        provider="codex-oauth",
        capability_source="codex-local-catalog/direct-responses-v1",
    )


def test_rejects_once_transient_429_for_xai_capability() -> None:
    assert not allows_once_transient_429(
        provider="xai",
        capability_source="omo-xai-models-store/openai-responses-v1",
    )
