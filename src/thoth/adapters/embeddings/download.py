"""A grant-bound fixed asset download, isolated from ordinary model/research egress."""

import asyncio
import hashlib
import os
import shutil
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from urllib.parse import quote, urljoin, urlsplit

import httpx

from thoth.adapters.embeddings.asset_files import check_time, ensure_directory, native_path
from thoth.adapters.embeddings.assets import LocalModelAssets
from thoth.domain.model_asset import ModelAssetDownloadGrant, ModelAssetError, ModelAssetStatus


def _allowed_host(hostname: str | None) -> bool:
    """huggingface.co and hf.co with their subdomains; whole names only, never prefix or contains.

    Hugging Face moves large files between CDN names (cdn-lfs.huggingface.co,
    cas-bridge.xethub.hf.co, us.aws.cdn.hf.co, ...), so the CDN name itself is not fixed here.
    Integrity does not rest on the host: every file's size and SHA-256 are checked against the
    pinned manifest.
    """
    if hostname is None or not hostname.isascii():
        return False
    name = hostname.lower()
    if not all(name.split(".")):
        return False
    return name in {"huggingface.co", "hf.co"} or name.endswith((".huggingface.co", ".hf.co"))


_CLEANUPS: set[asyncio.Task[None]] = set()


async def _cleanup(task: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), 3)
    except (TimeoutError, asyncio.CancelledError) as exc:
        _CLEANUPS.add(task)
        task.add_done_callback(_CLEANUPS.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        raise ModelAssetError("MODEL_ASSET_CLEANUP_UNCONFIRMED") from exc


@asynccontextmanager
async def _response(
    client: httpx.AsyncClient, url: str, timeout: float
) -> AsyncGenerator[httpx.Response]:
    request = client.build_request(
        "GET", url, timeout=timeout, headers={"Accept-Encoding": "identity"}
    )
    response = await client.send(request, stream=True)
    try:
        yield response
    finally:
        await _cleanup(asyncio.create_task(response.aclose()))


def _check_url(url: str, lfs_host: str | None) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ModelAssetError("MODEL_DOWNLOAD_DESTINATION_DENIED") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not _allowed_host(parsed.hostname)
        or (lfs_host is not None and parsed.hostname != lfs_host)
    ):
        raise ModelAssetError("MODEL_DOWNLOAD_DESTINATION_DENIED")
    assert parsed.hostname is not None
    return parsed.hostname


async def _validate_redirect(response: httpx.Response) -> None:
    # httpx constructs a next_request even with follow_redirects=False. Validate
    # its untrusted Location before that parser can turn it into an opaque failure.
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if location is None:
            raise ModelAssetError("MODEL_DOWNLOAD_DESTINATION_DENIED")
        try:
            _check_url(urljoin(str(response.request.url), location), None)
        except ValueError as exc:
            raise ModelAssetError("MODEL_DOWNLOAD_DESTINATION_DENIED") from exc


class FixedModelDownload:
    def __init__(
        self, assets: LocalModelAssets, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.assets, self.transport = assets, transport

    async def _file(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        target: os.PathLike[str],
        expected_bytes: int,
        expected_sha: str,
        deadline: float,
        progress: Callable[[int], None],
        check_authority: Callable[[], None],
    ) -> None:
        lfs_host: str | None = None
        for _ in range(4):
            check_time(deadline)
            check_authority()
            host = _check_url(url, lfs_host)
            if host != "huggingface.co":
                lfs_host = host
            client.cookies.clear()
            timeout = min(10.0, max(0.001, deadline - time.monotonic()))
            async with _response(client, url, timeout) as response:
                check_authority()
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if location is None:
                        raise ModelAssetError("MODEL_DOWNLOAD_DESTINATION_DENIED")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200 or response.headers.get(
                    "content-encoding", "identity"
                ) not in ("", "identity"):
                    raise ModelAssetError("MODEL_DOWNLOAD_FAILED")
                length = response.headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) != expected_bytes):
                    raise ModelAssetError("MODEL_HASH_MISMATCH")
                digest, count = hashlib.sha256(), 0
                with open(target, "xb") as stream:
                    async for chunk in response.aiter_raw():
                        check_time(deadline)
                        check_authority()
                        count += len(chunk)
                        if count > expected_bytes:
                            raise ModelAssetError("MODEL_HASH_MISMATCH")
                        digest.update(chunk)
                        stream.write(chunk)
                        progress(count)
                    stream.flush()
                    os.fsync(stream.fileno())
                if count != expected_bytes or digest.hexdigest() != expected_sha:
                    raise ModelAssetError("MODEL_HASH_MISMATCH")
                return
        raise ModelAssetError("MODEL_DOWNLOAD_REDIRECT_LIMIT")

    async def install(
        self,
        grant: ModelAssetDownloadGrant,
        *,
        deadline: float,
        progress: Callable[[str, int], None],
        check_authority: Callable[[], None],
    ) -> ModelAssetStatus:
        manifest = self.assets.manifest
        if not grant.approved or grant.manifest_digest != manifest.digest:
            raise ModelAssetError("MODEL_ASSET_CONSENT_REQUIRED")
        check_authority()
        check_time(deadline)
        if native_path(self.assets.destination).exists():
            async with asyncio.timeout_at(deadline):
                await asyncio.to_thread(self.assets.verify, self.assets.destination, deadline)
            check_authority()
            return ModelAssetStatus(
                state="READY", manifest_digest=manifest.digest, total_bytes=manifest.total_bytes
            )
        parent = self.assets.root / ".downloads"
        ensure_directory(parent)
        staging = parent / uuid.uuid4().hex
        native_path(staging).mkdir()
        try:
            async with asyncio.timeout_at(deadline):
                client = httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    event_hooks={"response": [_validate_redirect]},
                )
                try:
                    completed = 0
                    for spec in manifest.files:
                        target = staging.joinpath(*spec.path.split("/"))
                        ensure_directory(target.parent)
                        url = (
                            f"https://huggingface.co/{manifest.model_id}/resolve/"
                            f"{manifest.revision}/{quote(spec.path, safe='/')}"
                        )
                        prior = completed
                        await self._file(
                            client,
                            url,
                            target=native_path(target),
                            expected_bytes=spec.bytes,
                            expected_sha=spec.sha256,
                            deadline=deadline,
                            progress=lambda count, prior=prior: progress(
                                "DOWNLOADING", prior + count
                            ),
                            check_authority=check_authority,
                        )
                        completed += spec.bytes
                finally:
                    await _cleanup(asyncio.create_task(client.aclose()))
                check_authority()
                progress("VERIFYING", completed)
                return await self.assets.import_directory(
                    staging, deadline=deadline, check_authority=check_authority
                )
        except httpx.HTTPError as exc:
            raise ModelAssetError("MODEL_DOWNLOAD_FAILED") from exc
        finally:
            if staging.parent != parent:
                raise ModelAssetError("MODEL_ASSET_CLEANUP_UNCONFIRMED")
            await _cleanup(
                asyncio.create_task(asyncio.to_thread(shutil.rmtree, native_path(staging)))
            )
