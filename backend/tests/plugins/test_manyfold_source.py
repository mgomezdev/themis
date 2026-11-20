"""Versioned v0 wire shapes against a local transport, never a live library."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from app.plugins.manyfold.provider import ManyfoldError, ManyfoldSource
from app.plugins.manyfold.settings import ManyfoldSettings

BASE = "https://manyfold.test"
FILE = "/models/m/model_files/f"
MODEL = {
    "@id": "/models/m",
    "name": "Model",
    "hasPart": [{"@id": FILE}],
    "creator": {"@id": "/creators/c"},
    "isPartOf": [{"@id": "/collections/one", "name": "Collection"}],
    "spdx:license": {
        "licenseId": "CC-BY-4.0",
        "@id": "https://creativecommons.org/licenses/by/4.0/",
    },
}
DETAIL = {
    "@id": FILE,
    "filename": "part.stl",
    "encodingFormat": "model/stl",
    "contentSize": 5,
    "contentUrl": "/models/m/raw/part.stl",
    "isPartOf": {"@id": "/models/m"},
}


def source(handler=None, **settings):
    requests = []

    def handle(req):
        requests.append(req)
        if handler:
            answer = handler(req)
            if answer is not None:
                return answer
        if req.url.path == "/oauth/token":
            assert req.method == "POST"
            form = parse_qs(req.content.decode())
            assert form == {
                "grant_type": ["client_credentials"],
                "client_id": ["client"],
                "client_secret": ["fixture-secret"],
                "scope": ["public read"],
            }
            return httpx.Response(
                200, json={"access_token": "fixture-token", "expires_in": 7200}
            )
        assert req.headers["Authorization"].startswith("Bearer ")
        if req.url.path == "/models":
            return httpx.Response(
                200, json={"member": [{"@id": "/models/m"}], "view": {}}
            )
        if req.url.path == "/models/m":
            return httpx.Response(200, json=MODEL)
        if req.url.path == FILE:
            return httpx.Response(200, json=DETAIL)
        if req.url.path == "/creators/c":
            return httpx.Response(
                200, json={"name": "Artist", "links": [{"url": "https://artist.test"}]}
            )
        if req.url.path == "/models/m/raw/part.stl":
            return httpx.Response(200, content=b"model", headers={"ETag": "v1"})
        raise AssertionError(f"Unexpected fixture route {req.url.path}")

    return ManyfoldSource(
        ManyfoldSettings(
            url=BASE, client_id="client", client_secret="fixture-secret", **settings
        ),
        transport=httpx.MockTransport(handle),
    ), requests


async def test_catalog_metadata_inherits_creator_license_and_collections_without_downloading_bytes():
    provider, requests = source()
    page = await provider.list_catalog(changed_since="unsupported-hint")
    assert page.full_scan and page.next_cursor is None
    (file,) = page.entries
    assert file.source_file_id == file.source_url == BASE + FILE
    assert (
        file.name == "part.stl"
        and file.media_type == "model/stl"
        and file.size_bytes == 5
    )
    assert file.creator.name == "Artist" and file.creator.links == (
        "https://artist.test",
    )
    assert file.license_code == "CC-BY-4.0" and file.collections == ("Collection",)
    assert not any("/raw/" in r.url.path for r in requests)
    assert all(r.method == "GET" or r.url.path == "/oauth/token" for r in requests)


async def test_concurrent_reads_share_one_token_and_401_refreshes_once():
    tokens = 0

    def handler(req):
        nonlocal tokens
        if req.url.path == "/oauth/token":
            tokens += 1
            return httpx.Response(
                200, json={"access_token": f"token-{tokens}", "expires_in": 7200}
            )
        if (
            req.url.path == "/models"
            and req.headers["Authorization"] == "Bearer token-1"
        ):
            return httpx.Response(401)

    provider, _ = source(handler)
    await asyncio.gather(provider.test_connection(), provider.test_connection())
    assert tokens == 2


async def test_hydra_next_page_is_same_origin_and_is_an_authoritative_sweep():
    def handler(req):
        if req.url.path == "/models":
            return httpx.Response(
                200, json={"member": [], "view": {"next": "/models?page=2"}}
            )

    provider, _ = source(handler)
    page = await provider.list_catalog()
    assert page.next_cursor == BASE + "/models?page=2" and page.full_scan


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"member": [], "view": {"next": "https://other.test/models?page=2"}},
        {"member": [], "view": {"next": "/oauth/token"}},
    ],
)
async def test_malformed_or_foreign_pages_fail_instead_of_becoming_an_empty_complete_catalog(
    body,
):
    provider, _ = source(
        lambda r: httpx.Response(200, json=body) if r.url.path == "/models" else None
    )
    with pytest.raises(ManyfoldError):
        await provider.list_catalog()


async def test_download_preserves_bytes_and_checks_revision_and_limit():
    provider, _ = source()
    async with provider.download(BASE + FILE, revision="v1") as stream:
        assert b"".join([c async for c in stream.chunks]) == b"model"
        assert stream.revision == "v1"
    with pytest.raises(ManyfoldError, match="changed"):
        async with provider.download(BASE + FILE, revision="old"):
            pass
    provider, _ = source(max_download_bytes=4)
    with pytest.raises(ManyfoldError, match="limit"):
        async with provider.download(BASE + FILE):
            pass


async def test_foreign_content_url_never_receives_credentials():
    detail = {**DETAIL, "contentUrl": "https://unrelated.test/private.stl"}
    provider, requests = source(
        lambda r: httpx.Response(200, json=detail) if r.url.path == FILE else None
    )
    with pytest.raises(ManyfoldError, match="origin"):
        async with provider.download(BASE + FILE):
            pass
    assert all(r.url.host == "manyfold.test" for r in requests)


@pytest.mark.parametrize("status", [302, 400, 401, 403, 500])
async def test_token_errors_are_actionable_and_never_echo_source_error_bodies(status):
    provider, _ = source(
        lambda r: (
            httpx.Response(status, text="fixture-secret fixture-token")
            if r.url.path == "/oauth/token"
            else None
        )
    )
    with pytest.raises(ManyfoldError) as exc:
        await provider.test_connection()
    assert "fixture-secret" not in str(exc.value) and "fixture-token" not in str(
        exc.value
    )


async def test_stream_size_is_bounded_without_content_length_and_closed_on_failure():
    class Bytes(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"123"
            yield b"456"

        async def aclose(self):
            self.closed = True

    data = Bytes()
    provider, _ = source(
        lambda r: httpx.Response(200, stream=data) if "/raw/" in r.url.path else None,
        max_download_bytes=4,
    )
    with pytest.raises(ManyfoldError, match="limit"):
        async with provider.download(BASE + FILE) as stream:
            async for _ in stream.chunks:
                pass
    assert data.closed


async def test_only_missing_file_metadata_is_a_confirmed_absence():
    from app.plugins.capabilities.library_source import SourceFileMissing

    provider, _ = source(lambda r: httpx.Response(404) if r.url.path == FILE else None)
    with pytest.raises(SourceFileMissing):
        await provider.describe(BASE + FILE)
    provider, _ = source(
        lambda r: httpx.Response(404) if r.url.path == "/creators/c" else None
    )
    with pytest.raises(ManyfoldError):
        await provider.describe(BASE + FILE)
