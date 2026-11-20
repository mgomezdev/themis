"""Read-only Manyfold v0 source; credentials and wire shapes stay inside this plugin."""

from __future__ import annotations

import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..capabilities.library_source import (
    CatalogPage,
    SourceCreator,
    SourceDownload,
    SourceFile,
    SourceFileMissing,
)
from .settings import ManyfoldSettings

MEDIA = "application/vnd.manyfold.v0+json"
MAX_JSON_BYTES = 8 * 1024 * 1024


class ManyfoldError(ValueError):
    pass


def _origin(url):
    return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80)


class ManyfoldSource:
    def __init__(self, settings: ManyfoldSettings, *, transport=None):
        if not settings.url or not settings.client_id or not settings.client_secret:
            raise ManyfoldError(
                "Set the Manyfold URL, OAuth client ID and client secret"
            )
        base = urlsplit(settings.url.strip().rstrip("/"))
        if (
            base.scheme not in {"http", "https"}
            or not base.hostname
            or base.username
            or base.password
            or base.query
            or base.fragment
        ):
            raise ManyfoldError(
                "Use an HTTP(S) Manyfold base URL without credentials, query or fragment"
            )
        self.base = str(httpx.URL(urlunsplit(base))).rstrip("/")
        self.settings, self.transport = settings, transport
        self._token, self._expires = None, 0
        self._token_lock = asyncio.Lock()

    def _client(self):
        return httpx.AsyncClient(
            timeout=self.settings.request_timeout_s,
            follow_redirects=False,
            transport=self.transport,
        )

    def _url(self, path, *, query=False):
        if not isinstance(path, str) or not path:
            raise ManyfoldError("Manyfold returned a missing resource URL")
        url = urlsplit(urljoin(self.base + "/", path))
        if (
            _origin(url) != _origin(urlsplit(self.base))
            or url.username
            or url.password
            or url.fragment
            or (url.query and not query)
        ):
            raise ManyfoldError(
                "Manyfold returned a resource outside its configured origin or an invalid page URL"
            )
        return str(
            httpx.URL(urlunsplit(url._replace(path=url.path.rstrip("/") or "/")))
        )

    def _status(self, response, *, file_identity=False):
        if response.status_code == 401:
            raise ManyfoldError("Manyfold rejected the OAuth credentials or token")
        if response.status_code == 403:
            raise ManyfoldError(
                "Manyfold requires public/read scope for this operation"
            )
        if response.status_code == 404:
            if file_identity:
                raise SourceFileMissing("Manyfold file is no longer accessible")
            raise ManyfoldError("Manyfold resource is missing or inaccessible")
        if response.status_code == 412:
            raise ManyfoldError("Manyfold file changed; refresh before using it")
        if not 200 <= response.status_code < 300:
            raise ManyfoldError(
                f"Manyfold request failed (HTTP {response.status_code})"
            )

    async def _response(self, client, method, url, **kwargs):
        # Bound decompressed JSON as well as file bytes; do not retain error bodies
        # that may echo credentials, tokens or signed URLs.
        async with client.stream(method, url, **kwargs) as response:
            if not 200 <= response.status_code < 300:
                return response.status_code, None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_JSON_BYTES:
                    raise ManyfoldError(
                        "Manyfold API response exceeds the metadata limit"
                    )
            return response.status_code, json.loads(body)

    async def _access_token(self, rejected=None):
        async with self._token_lock:
            if (
                self._token
                and time.monotonic() < self._expires
                and self._token != rejected
            ):
                return self._token
            try:
                async with self._client() as client:
                    status, data = await self._response(
                        client,
                        "POST",
                        self._url("oauth/token"),
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self.settings.client_id,
                            "client_secret": self.settings.client_secret,
                            "scope": "public read",
                        },
                    )
                self._status(httpx.Response(status))
                token, ttl = data.get("access_token"), data.get("expires_in")
                if (
                    not isinstance(token, str)
                    or not token
                    or not isinstance(ttl, (int, float))
                    or isinstance(ttl, bool)
                    or not math.isfinite(ttl)
                    or ttl <= 0
                ):
                    raise ManyfoldError(
                        "Manyfold returned an invalid OAuth token response"
                    )
                self._token, self._expires = (
                    token,
                    time.monotonic() + max(0, ttl - min(30, ttl / 10)),
                )
                return token
            except (httpx.HTTPError, ValueError, AttributeError) as exc:
                if isinstance(exc, ManyfoldError):
                    raise
                raise ManyfoldError(
                    "Could not obtain a Manyfold token; check URL, TLS and OAuth credentials"
                ) from None

    async def _json(self, path, *, query=False, file_identity=False):
        url = self._url(path, query=query)
        rejected = None
        for _ in range(2):
            token = await self._access_token(rejected)
            try:
                async with self._client() as client:
                    status, data = await self._response(
                        client,
                        "GET",
                        url,
                        headers={"Authorization": f"Bearer {token}", "Accept": MEDIA},
                    )
                if status == 401 and rejected is None:
                    rejected = token
                    continue
                self._status(httpx.Response(status), file_identity=file_identity)
                if not isinstance(data, dict):
                    raise ManyfoldError("Manyfold returned an invalid JSON object")
                return data
            except httpx.HTTPError:
                raise ManyfoldError(
                    "Manyfold is unreachable; check its URL, TLS and availability"
                ) from None
            except ValueError as exc:
                if isinstance(exc, (ManyfoldError, SourceFileMissing)):
                    raise
                raise ManyfoldError(
                    "Manyfold returned invalid API JSON; check API support"
                ) from None
        raise ManyfoldError("Manyfold rejected the refreshed access token")

    async def test_connection(self):
        data = await self._json("models?page=1", query=True)
        if not isinstance(data.get("member"), list):
            raise ManyfoldError(
                "Manyfold did not return a catalog; check v0 API support"
            )
        return {
            "ok": True,
            "message": "Connected to the Manyfold v0 catalog (read-only)",
        }

    async def _file(self, path, model=None):
        url = self._url(path)
        data = await self._json(url, file_identity=True)
        if self._url(data.get("@id")) != url:
            raise ManyfoldError(
                "Manyfold file identity did not match the requested resource"
            )
        if model is None:
            parent = data.get("isPartOf") or {}
            model = await self._json(parent.get("@id"))
        creator_data = data.get("creator") or model.get("creator")
        creator = None
        if creator_data:
            creator_url = self._url(creator_data.get("@id"))
            details = await self._json(creator_url)
            creator = SourceCreator(
                creator_url,
                str(details.get("name") or ""),
                tuple(
                    row["url"]
                    for row in details.get("links", [])
                    if isinstance(row, dict)
                    and isinstance(row.get("url"), str)
                    and urlsplit(row["url"]).scheme in {"http", "https"}
                ),
            )
        license_data = data.get("spdx:license") or model.get("spdx:license") or {}
        collections = model.get("isPartOf") or []
        if not isinstance(collections, list):
            raise ManyfoldError("Manyfold returned invalid collection metadata")
        descriptor = SourceFile(
            source_file_id=url,
            name=str(data.get("filename") or data.get("name") or ""),
            source_url=url,
            media_type=data.get("encodingFormat"),
            size_bytes=data.get("contentSize"),
            creator=creator,
            license_code=license_data.get("licenseId"),
            license_url=license_data.get("@id"),
            collections=tuple(
                str(c.get("name") or c.get("@id") or "") for c in collections
            ),
        )
        if not descriptor.name:
            raise ManyfoldError("Manyfold returned a file without a name")
        return descriptor, data

    async def describe(self, source_file_id):
        return (await self._file(source_file_id))[0]

    async def list_catalog(self, cursor=None, changed_since=None):
        # v0.153 has no changed_since parameter. Never claim partial data is a full catalog.
        data = await self._json(cursor or "models?page=1", query=True)
        if not isinstance(data.get("member"), list) or not isinstance(
            data.get("view"), dict
        ):
            raise ManyfoldError("Manyfold returned an incomplete catalog page")
        entries = []
        for member in data["member"]:
            model = await self._json(member.get("@id"))
            if not isinstance(model.get("hasPart"), list):
                raise ManyfoldError("Manyfold returned incomplete model files")
            for file in model["hasPart"]:
                entries.append((await self._file(file.get("@id"), model))[0])
        nxt = data["view"].get("next")
        if nxt is not None:
            nxt = self._url(nxt, query=True)
            if urlsplit(nxt).path != urlsplit(self._url("models")).path:
                raise ManyfoldError("Manyfold returned an unexpected catalog page path")
        return CatalogPage(tuple(entries), nxt, full_scan=True, confirm_missing=True)

    @asynccontextmanager
    async def download(self, source_file_id, revision=None):
        _, data = await self._file(source_file_id)
        url = self._url(data.get("contentUrl"))
        rejected = None
        for _ in range(2):
            token = await self._access_token(rejected)
            headers = {"Authorization": f"Bearer {token}"}
            if revision is not None:
                headers["If-Match"] = revision
            try:
                async with self._client() as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        if response.status_code == 401 and rejected is None:
                            rejected = token
                            continue
                        self._status(response)
                        etag = response.headers.get("ETag")
                        if revision is not None and etag != revision:
                            raise ManyfoldError(
                                "Manyfold file changed; refresh its metadata before using it"
                            )
                        length = response.headers.get("Content-Length")
                        size = (
                            int(length)
                            if length is not None and length.isdecimal()
                            else None
                        )
                        if (
                            response.headers.get(
                                "Content-Encoding", "identity"
                            ).casefold()
                            != "identity"
                        ):
                            size = None  # Content-Length describes encoded bytes, chunks are decoded.
                        if size is not None and size > self.settings.max_download_bytes:
                            raise ManyfoldError(
                                "Manyfold file exceeds the configured download limit"
                            )

                        async def chunks():
                            total = 0
                            async for chunk in response.aiter_bytes():
                                total += len(chunk)
                                if total > self.settings.max_download_bytes:
                                    raise ManyfoldError(
                                        "Manyfold file exceeds the configured download limit"
                                    )
                                yield chunk
                            if size is not None and total != size:
                                raise ManyfoldError(
                                    "Manyfold download ended before its declared size"
                                )

                        yield SourceDownload(
                            chunks(), etag, size, response.headers.get("Content-Type")
                        )
                        return
            except httpx.HTTPError:
                raise ManyfoldError(
                    "Manyfold download failed; retry when the source is available"
                ) from None
        raise ManyfoldError("Manyfold rejected the refreshed access token")
