"""Fixed destination, bounded streams and publication; every HTTP response is synthetic."""

import asyncio
import time
from pathlib import Path

import httpx
import pytest
from tests.embedding_fixtures import sample_assets

from thoth.adapters.embeddings.download import FixedModelDownload
from thoth.domain.model_asset import ModelAssetDownloadGrant, ModelAssetError


class Bytes(httpx.AsyncByteStream):
    def __init__(self, data: bytes, stall: bool = False):
        self.data, self.stall, self.closed = data, stall, False

    async def __aiter__(self):
        if self.stall:
            await asyncio.Event().wait()
        yield self.data

    async def aclose(self):
        self.closed = True


def grant(digest: str):
    return ModelAssetDownloadGrant(
        manifest_digest=digest,
        operation_id="operation:synthetic",
        actor_ref="local:operator",
        approved=True,
    )


async def test_mock_download_verifies_bytes_and_fixed_url(tmp_path: Path):
    source, assets = sample_assets(tmp_path)
    data = (source / "model.bin").read_bytes()
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, stream=Bytes(data))

    downloader = FixedModelDownload(assets, httpx.MockTransport(transport))
    progress: list[tuple[str, int]] = []
    result = await downloader.install(
        grant(assets.manifest.digest),
        deadline=time.monotonic() + 5,
        progress=lambda state, count: progress.append((state, count)),
        check_authority=lambda: None,
    )
    assert result.state == "READY" and assets.status().state == "READY"
    assert (
        str(calls[0].url)
        == "https://huggingface.co/synthetic/model/resolve/" + "a" * 40 + "/model.bin"
    )
    assert "authorization" not in calls[0].headers and "cookie" not in calls[0].headers
    assert progress[-1] == ("VERIFYING", len(data))
    assert not list((assets.root / ".downloads").iterdir())


@pytest.mark.parametrize(
    "destination",
    [
        "http://huggingface.co/evil",
        "https://evil.example/model",
        "https://huggingface.co:123/model",
        "https://huggingface.co:invalid/model",
        "https://user:pass@huggingface.co/model",
    ],
)
async def test_redirect_denied_before_second_request(tmp_path: Path, destination: str):
    _, assets = sample_assets(tmp_path)
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": destination})

    downloader = FixedModelDownload(assets, httpx.MockTransport(transport))
    with pytest.raises(ModelAssetError, match="DESTINATION_DENIED"):
        await downloader.install(
            grant(assets.manifest.digest),
            deadline=time.monotonic() + 5,
            progress=lambda _state, _count: None,
            check_authority=lambda: None,
        )
    assert len(calls) == 1 and not assets.destination.exists()


async def test_stalled_body_obeys_total_deadline_and_closes(tmp_path: Path):
    _, assets = sample_assets(tmp_path)
    stream = Bytes(b"", stall=True)
    downloader = FixedModelDownload(
        assets, httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    )
    with pytest.raises(TimeoutError):
        await downloader.install(
            grant(assets.manifest.digest),
            deadline=time.monotonic() + 0.1,
            progress=lambda _state, _count: None,
            check_authority=lambda: None,
        )
    assert stream.closed and not assets.destination.exists()
    assert not list((assets.root / ".downloads").iterdir())


@pytest.mark.parametrize("same_size", [False, True], ids=["shorter", "same-size"])
async def test_corrupt_bytes_never_published(
    tmp_path: Path, same_size: bool, monkeypatch: pytest.MonkeyPatch
):
    source, assets = sample_assets(tmp_path)
    original = (source / "model.bin").read_bytes()
    corrupted = bytes([original[0] ^ 1]) + original[1:] if same_size else original[:-1]
    assert (len(corrupted) == len(original)) is same_size

    async def forbidden_import(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CORRUPT_DOWNLOAD_REACHED_IMPORT")

    # The downloader must reject invalid network bytes before asking the store to copy them.
    monkeypatch.setattr(assets, "import_directory", forbidden_import)
    downloader = FixedModelDownload(
        assets, httpx.MockTransport(lambda _: httpx.Response(200, stream=Bytes(corrupted)))
    )
    with pytest.raises(ModelAssetError, match="HASH_MISMATCH"):
        await downloader.install(
            grant(assets.manifest.digest),
            deadline=time.monotonic() + 5,
            progress=lambda _state, _count: None,
            check_authority=lambda: None,
        )
    assert not assets.destination.exists()
