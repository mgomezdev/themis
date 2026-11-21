"""Versioned v0 wire shapes against a local transport, never a live library."""

import asyncio
import json
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


@pytest.mark.parametrize(
    "url",
    [
        "ftp://manyfold.test",
        "https://user:fixture-secret@manyfold.test",
        "https://manyfold.test?token=fixture-secret",
        "https://manyfold.test/#private",
        "https://manyfold.test:bad",
        "https://[broken",
    ],
)
def test_invalid_configuration_is_rejected_without_echoing_credentials(url):
    with pytest.raises(ManyfoldError) as failure:
        ManyfoldSource(
            ManyfoldSettings(
                url=url, client_id="client", client_secret="fixture-secret"
            )
        )
    assert "fixture-secret" not in str(failure.value)


def test_unconfigured_plugin_has_an_actionable_error():
    with pytest.raises(ManyfoldError, match="client ID"):
        ManyfoldSource(ManyfoldSettings())


@pytest.mark.parametrize(
    "token",
    [
        None,
        [],
        {},
        {"access_token": "", "expires_in": 10},
        {"access_token": "private-token", "expires_in": True},
        {"access_token": "private-token", "expires_in": -1},
        {"access_token": "private-token", "expires_in": float("inf")},
        {"access_token": "private-token", "expires_in": "7200"},
    ],
)
async def test_malformed_oauth_success_is_rejected_without_echoing_token(token):
    provider, _ = source(
        lambda r: (
            httpx.Response(200, content=json.dumps(token).encode())
            if r.url.path == "/oauth/token"
            else None
        )
    )
    with pytest.raises(ManyfoldError) as failure:
        await provider.test_connection()
    assert "private-token" not in str(failure.value)


@pytest.mark.parametrize(
    "path,body",
    [
        ("/models", {"member": [None], "view": {}}),
        ("/models/m", {**MODEL, "hasPart": [None]}),
        ("/models/m", {**MODEL, "hasPart": None}),
        ("/models/m", {**MODEL, "isPartOf": [None]}),
        ("/models/m", {**MODEL, "isPartOf": {"name": "bad"}}),
        ("/models/m", {**MODEL, "isPartOf": [{"name": {}}]}),
        (FILE, {**DETAIL, "creator": "private-wire-value"}),
        (FILE, {**DETAIL, "spdx:license": "private-wire-value"}),
        (FILE, {**DETAIL, "spdx:license": {"licenseId": {"private-wire-value": True}}}),
        (FILE, {**DETAIL, "spdx:license": {"@id": []}}),
        (FILE, {**DETAIL, "filename": {"private-wire-value": True}}),
        (FILE, {**DETAIL, "encodingFormat": []}),
        (FILE, {**DETAIL, "contentSize": True}),
        (FILE, {**DETAIL, "contentSize": -1}),
        (FILE, {**DETAIL, "filename": "", "name": ""}),
        (FILE, {**DETAIL, "@id": "/models/m/model_files/other"}),
        ("/creators/c", {"name": {}, "links": []}),
        ("/creators/c", {"name": "Artist", "links": {"private-wire-value": True}}),
    ],
)
async def test_malformed_nested_catalog_data_fails_closed_with_a_safe_error(path, body):
    provider, _ = source(
        lambda r: httpx.Response(200, json=body) if r.url.path == path else None
    )
    with pytest.raises(ManyfoldError) as failure:
        await provider.list_catalog()
    assert "private-wire-value" not in str(failure.value)


async def test_bad_parent_reference_is_not_treated_as_an_absent_file():
    provider, _ = source(
        lambda r: (
            httpx.Response(200, json={**DETAIL, "isPartOf": []})
            if r.url.path == FILE
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="parent model"):
        await provider.describe(BASE + FILE)


@pytest.mark.parametrize(
    "resource",
    [
        "",
        "https://manyfold.test:bad/file",
        "https://[broken",
        "https://manyfold.test/file?token=private-wire-value",
        "https://user:private-wire-value@manyfold.test/file",
    ],
)
async def test_invalid_resource_urls_are_rejected_before_an_authenticated_request(
    resource,
):
    provider, requests = source()
    with pytest.raises(ManyfoldError) as failure:
        await provider.describe(resource)
    assert requests == [] and "private-wire-value" not in str(failure.value)


@pytest.mark.parametrize("path", ["/oauth/token", "/models", "/models/m/raw/part.stl"])
async def test_transport_errors_are_safe_for_token_metadata_and_download(path):
    def fail(request):
        if request.url.path == path:
            raise httpx.ConnectError("fixture-secret private-token", request=request)

    provider, _ = source(fail)
    with pytest.raises(ManyfoldError) as failure:
        if "/raw/" in path:
            async with provider.download(BASE + FILE) as stream:
                async for _ in stream.chunks:
                    pass
        else:
            await provider.test_connection()
    assert "fixture-secret" not in str(failure.value) and "private-token" not in str(
        failure.value
    )


@pytest.mark.parametrize("path", ["/models", "/models/m/raw/part.stl"])
async def test_repeated_401_stops_after_one_token_refresh(path):
    provider, requests = source(
        lambda r: httpx.Response(401) if r.url.path == path else None
    )
    with pytest.raises(ManyfoldError, match="credentials|token"):
        if "/raw/" in path:
            async with provider.download(BASE + FILE):
                pass
        else:
            await provider.test_connection()
    assert sum(r.url.path == "/oauth/token" for r in requests) == 2
    assert sum(r.url.path == path for r in requests) == 2


async def test_expired_token_is_refreshed_using_the_response_expiry(monkeypatch):
    from app.plugins.manyfold import provider as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    provider, requests = source()
    await provider.test_connection()
    clock[0] = 7300.0
    await provider.test_connection()
    assert sum(r.url.path == "/oauth/token" for r in requests) == 2


@pytest.mark.parametrize("body", [b"not JSON private-wire-value", b"[]"])
async def test_invalid_api_json_never_echoes_response_body(body):
    provider, _ = source(
        lambda r: httpx.Response(200, content=body) if r.url.path == "/models" else None
    )
    with pytest.raises(ManyfoldError) as failure:
        await provider.test_connection()
    assert "private-wire-value" not in str(failure.value)


async def test_metadata_limit_applies_to_decoded_response_bytes(monkeypatch):
    import gzip
    from app.plugins.manyfold import provider as module

    monkeypatch.setattr(module, "MAX_JSON_BYTES", 128)
    body = gzip.compress(b'{"private":"' + b"x" * 1000 + b'"}')
    provider, _ = source(
        lambda r: (
            httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"})
            if r.url.path == "/models"
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="metadata limit"):
        await provider.list_catalog()


async def test_compressed_download_uses_decoded_size_not_wire_length():
    import gzip

    provider, _ = source(
        lambda r: (
            httpx.Response(
                200,
                content=gzip.compress(b"model"),
                headers={"Content-Encoding": "gzip"},
            )
            if "/raw/" in r.url.path
            else None
        ),
        max_download_bytes=5,
    )
    async with provider.download(BASE + FILE) as stream:
        assert stream.size_bytes is None
        assert b"".join([chunk async for chunk in stream.chunks]) == b"model"


async def test_truncated_download_closes_its_stream():
    class Truncated(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"mod"

        async def aclose(self):
            self.closed = True

    data = Truncated()
    provider, _ = source(
        lambda r: (
            httpx.Response(200, stream=data, headers={"Content-Length": "5"})
            if "/raw/" in r.url.path
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="declared size"):
        async with provider.download(BASE + FILE) as stream:
            async for _ in stream.chunks:
                pass
    assert data.closed


async def test_extreme_token_expiry_does_not_escape_as_an_overflow():
    provider, _ = source(
        lambda r: (
            httpx.Response(
                200, json={"access_token": "private-token", "expires_in": 10**400}
            )
            if r.url.path == "/oauth/token"
            else None
        )
    )
    with pytest.raises(ManyfoldError) as failure:
        await provider.test_connection()
    assert "private-token" not in str(failure.value)


async def test_malformed_creator_link_does_not_echo_its_private_url():
    provider, _ = source(
        lambda r: (
            httpx.Response(
                200,
                json={
                    "name": "Artist",
                    "links": [{"url": "https://[private-wire-value]/"}],
                },
            )
            if r.url.path == "/creators/c"
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="creator link") as failure:
        await provider.describe(BASE + FILE)
    assert "private-wire-value" not in str(failure.value)


async def test_file_attribution_wins_without_using_invalid_parent_attribution():
    def handler(request):
        if request.url.path == FILE:
            return httpx.Response(
                200,
                json={
                    **DETAIL,
                    "creator": {"@id": "/creators/override"},
                    "spdx:license": {"licenseId": "CC0-1.0"},
                },
            )
        if request.url.path == "/models/m":
            return httpx.Response(
                200,
                json={
                    **MODEL,
                    "creator": "unused legacy value",
                    "spdx:license": "unused legacy value",
                },
            )
        if request.url.path == "/creators/override":
            return httpx.Response(200, json={"name": "File artist", "links": None})

    provider, _ = source(handler)
    file = await provider.describe(BASE + FILE)
    assert file.creator.name == "File artist" and file.creator.links == ()
    assert file.license_code == "CC0-1.0"


async def test_missing_attribution_remains_unknown_instead_of_being_invented():
    provider, _ = source(
        lambda r: (
            httpx.Response(200, json={**MODEL, "creator": None, "spdx:license": None})
            if r.url.path == "/models/m"
            else None
        )
    )
    file = await provider.describe(BASE + FILE)
    assert file.creator is None and file.license_code is None


@pytest.mark.parametrize("status", [302, 403, 412])
async def test_download_status_failure_never_follows_redirects_or_echoes_body(status):
    provider, requests = source(
        lambda r: (
            httpx.Response(
                status,
                text="private-wire-value",
                headers={"Location": "https://other.test/private"},
            )
            if "/raw/" in r.url.path
            else None
        )
    )
    with pytest.raises(ManyfoldError) as failure:
        async with provider.download(BASE + FILE):
            pass
    assert "private-wire-value" not in str(failure.value)
    assert all(request.url.host == "manyfold.test" for request in requests)
    assert sum("/raw/" in request.url.path for request in requests) == 1


@pytest.mark.parametrize("value", [False, "", {}])
async def test_falsy_invalid_collection_containers_are_not_treated_as_empty(value):
    provider, _ = source(
        lambda r: (
            httpx.Response(200, json={**MODEL, "isPartOf": value})
            if r.url.path == "/models/m"
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="collection"):
        await provider.list_catalog()


@pytest.mark.parametrize("value", [False, "", {}])
async def test_falsy_invalid_creator_link_containers_are_not_treated_as_empty(value):
    provider, _ = source(
        lambda r: (
            httpx.Response(200, json={"name": "Artist", "links": value})
            if r.url.path == "/creators/c"
            else None
        )
    )
    with pytest.raises(ManyfoldError, match="creator links"):
        await provider.describe(BASE + FILE)
