"""Tests for the Spoolman plugin client's PATCH calls (patch_filament, patch_spool_remaining).

Assumed Spoolman behaviors verified here:
  1. PATCH /api/v1/filament/{id} does a partial update of the `extra` dict
     (we send only the key we own; Spoolman preserves the rest).
  2. Sending {"extra": {"orca_profiles": "<json-string>"}} is the correct body format.
  3. A 400 from Spoolman is surfaced with the full response body, not just a status code.
  4. An X-API-Key header is included only when api_key is provided.
"""
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.plugins.spoolman import client as spoolman_client
from app.plugins.spoolman.client import patch_filament, patch_spool_remaining
from tests import spoolman_mock

URL = "http://spoolman.test"

BASE_URL = "http://spoolman.test"
FILAMENT_ID = 5


def _ok_response(data: dict) -> httpx.Response:
    return httpx.Response(200, json=data)


def _error_response(status: int, body: str, filament_id: int = FILAMENT_ID) -> httpx.Response:
    resp = httpx.Response(status, text=body, headers={"content-type": "text/plain"})
    resp._request = httpx.Request("PATCH", f"{BASE_URL}/api/v1/filament/{filament_id}")
    return resp


def _mock_client(patch_response: httpx.Response):
    """Return a context manager that patches httpx.AsyncClient."""
    mock_instance = AsyncMock()
    mock_instance.patch = AsyncMock(return_value=patch_response)

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield mock_instance

    return patch("app.plugins.spoolman.client.httpx.AsyncClient", side_effect=_ctx), mock_instance


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_sends_only_orca_profiles_in_extra():
    """We send ONLY the orca_profiles key — no other extra fields — relying on
    Spoolman's partial-update semantics to preserve any other custom fields."""
    profiles = {"Bambu Lab P1S 0.4 nozzle": ["Bambu PLA Basic @BBL P1S"]}
    returned = {"id": FILAMENT_ID, "extra": {"orca_profiles": json.dumps(profiles)}}

    ctx, mock_client = _mock_client(_ok_response(returned))
    with ctx:
        result = await patch_filament(BASE_URL, None, FILAMENT_ID, profiles)

    mock_client.patch.assert_called_once()
    call_kwargs = mock_client.patch.call_args

    sent_body = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs["json"]
    assert list(sent_body.keys()) == ["extra"], "Body must have exactly one top-level key: 'extra'"
    assert list(sent_body["extra"].keys()) == ["orca_profiles"], "extra must contain only 'orca_profiles'"


@pytest.mark.asyncio
async def test_patch_double_encodes_profiles():
    """Spoolman's text field type requires the value to be a JSON-encoded string
    whose CONTENT is itself valid JSON (double-encoded).  After one json.loads the
    result is a string; after two json.loads the result is the original dict."""
    profiles = {"Bambu Lab P1S 0.4 nozzle": ["Bambu PLA Basic @BBL P1S", "Bambu PLA Silk @BBL P1S"]}
    returned = {"id": FILAMENT_ID, "extra": {"orca_profiles": json.dumps(json.dumps(profiles))}}

    ctx, mock_client = _mock_client(_ok_response(returned))
    with ctx:
        await patch_filament(BASE_URL, None, FILAMENT_ID, profiles)

    sent_body = mock_client.patch.call_args.kwargs["json"]
    encoded = sent_body["extra"]["orca_profiles"]
    assert isinstance(encoded, str), "orca_profiles must be a string"
    once = json.loads(encoded)
    assert isinstance(once, str), "after one parse the result must be a string (double-encoded)"
    assert json.loads(once) == profiles, "after two parses the result must be the original dict"


@pytest.mark.asyncio
async def test_patch_empty_profiles_sends_double_encoded_empty_object():
    """Clearing all mappings sends '\"{}\"' — a JSON string whose content is '{}'."""
    returned = {"id": FILAMENT_ID, "extra": {"orca_profiles": json.dumps("{}")}}

    ctx, mock_client = _mock_client(_ok_response(returned))
    with ctx:
        await patch_filament(BASE_URL, None, FILAMENT_ID, {})

    sent_body = mock_client.patch.call_args.kwargs["json"]
    encoded = sent_body["extra"]["orca_profiles"]
    assert json.loads(encoded) == "{}", "first parse should yield the string '{}'"
    assert json.loads(json.loads(encoded)) == {}, "second parse should yield empty dict"


@pytest.mark.asyncio
async def test_patch_returns_spoolman_response():
    """Whatever Spoolman returns from PATCH is passed through unchanged."""
    profiles = {"P1S": ["Bambu PLA @P1S"]}
    spoolman_body = {
        "id": FILAMENT_ID,
        "name": "Elegoo Silk Min Green",
        "material": "PLA",
        "extra": {"orca_profiles": json.dumps(profiles), "color_hex": "00FF00"},
    }

    ctx, _ = _mock_client(_ok_response(spoolman_body))
    with ctx:
        result = await patch_filament(BASE_URL, None, FILAMENT_ID, profiles)

    assert result == spoolman_body


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_no_auth_header_when_api_key_is_none():
    """No X-API-Key header when api_key is None (open Spoolman instance)."""
    ctx, mock_client = _mock_client(_ok_response({"id": FILAMENT_ID}))
    with ctx:
        await patch_filament(BASE_URL, None, FILAMENT_ID, {})

    headers = mock_client.patch.call_args.kwargs.get("headers", {})
    assert "X-API-Key" not in headers


@pytest.mark.asyncio
async def test_patch_includes_api_key_header_when_set():
    """X-API-Key header is set when api_key is provided."""
    ctx, mock_client = _mock_client(_ok_response({"id": FILAMENT_ID}))
    with ctx:
        await patch_filament(BASE_URL, "secret-key", FILAMENT_ID, {})

    headers = mock_client.patch.call_args.kwargs.get("headers", {})
    assert headers.get("X-API-Key") == "secret-key"


# ---------------------------------------------------------------------------
# Error handling — assumed Spoolman 400 behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_400_raises_with_spoolman_body_in_message():
    """A 400 from Spoolman must surface the full response body so the operator
    can diagnose the validation error (e.g. unknown custom field key)."""
    error_body = '{"detail": "extra.orca_profiles: value is not a valid string"}'
    ctx, _ = _mock_client(_error_response(400, error_body))
    with ctx:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await patch_filament(BASE_URL, None, FILAMENT_ID, {"P1S": ["some profile"]})

    assert error_body in str(exc_info.value), "HTTPStatusError message must include Spoolman's response body"


@pytest.mark.asyncio
async def test_patch_422_raises_with_spoolman_body_in_message():
    """Same for 422 Unprocessable Entity — Spoolman's validation error text must propagate."""
    error_body = '{"detail": [{"loc": ["body", "extra"], "msg": "field required"}]}'
    ctx, _ = _mock_client(_error_response(422, error_body))
    with ctx:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await patch_filament(BASE_URL, None, FILAMENT_ID, {})

    assert error_body in str(exc_info.value)


@pytest.mark.asyncio
async def test_patch_target_url_is_correct():
    """Verify the PATCH URL includes the filament ID and uses Spoolman's v1 path."""
    ctx, mock_client = _mock_client(_ok_response({"id": FILAMENT_ID}))
    with ctx:
        await patch_filament("http://spoolman.test/", None, 42, {})

    call_args = mock_client.patch.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
    assert url == "http://spoolman.test/api/v1/filament/42"
    assert "//api" not in url, "Trailing slash on base URL must not produce double slash"


# ---------------------------------------------------------------------------
# patch_spool_remaining tests (absolute weight; there is deliberately no delta call)
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_patch_spool_remaining_sends_the_absolute_weight_to_the_spool_endpoint():
    ctx, mock_client = _mock_client(_ok_response({"id": 42, "remaining_weight": 480.0}))
    with ctx:
        result = await patch_spool_remaining("http://spoolman.test", "key123", spool_id=42, remaining_g=480.0)

    mock_client.patch.assert_called_once()
    call_args = mock_client.patch.call_args
    assert call_args.args[0] == "http://spoolman.test/api/v1/spool/42"
    assert call_args.kwargs["json"] == {"remaining_weight": 480.0}          # absolute, never a delta
    assert call_args.kwargs["headers"]["X-API-Key"] == "key123"
    assert result["remaining_weight"] == 480.0


@pytest.mark.asyncio
async def test_patch_spool_remaining_omits_the_key_header_and_strips_the_trailing_slash():
    ctx, mock_client = _mock_client(_ok_response({}))
    with ctx:
        await patch_spool_remaining("http://spoolman.test/", None, spool_id=7, remaining_g=5.0)

    call_args = mock_client.patch.call_args
    assert call_args.args[0] == "http://spoolman.test/api/v1/spool/7" and "//api" not in call_args.args[0]
    assert "X-API-Key" not in call_args.kwargs.get("headers", {})


@pytest.mark.asyncio
async def test_patch_spool_remaining_surfaces_the_error_body():
    ctx, _ = _mock_client(_error_response(400, "remaining_weight can only be used if the filament has a weight set."))
    with ctx, pytest.raises(httpx.HTTPStatusError, match="has a weight set"):
        await patch_spool_remaining("http://spoolman.test", None, spool_id=1, remaining_g=1.0)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

async def test_reads_return_the_upstream_payloads(spoolman_upstream):
    assert await spoolman_client.fetch_filaments(URL) == spoolman_mock._FILAMENTS
    assert await spoolman_client.fetch_spools(URL) == spoolman_mock._SPOOLS
    assert await spoolman_client.fetch_filament(URL, None, 2) == spoolman_mock._FILAMENTS[1]
    assert await spoolman_client.test_connection(URL) == {"version": "1.0.0-mock", "debug_mode": False}
    assert [(r.method, r.url.path) for r in spoolman_upstream.requests] == [
        ("GET", "/api/v1/filament"), ("GET", "/api/v1/spool"), ("GET", "/api/v1/filament/2"), ("GET", "/api/v1/info")]


async def test_reads_strip_the_trailing_slash_and_send_the_api_key_only_when_set(spoolman_upstream):
    await spoolman_client.fetch_spools(f"{URL}/", "secret")
    await spoolman_client.fetch_spools(URL, None)

    with_key, without_key = spoolman_upstream.requests
    assert str(with_key.url) == f"{URL}/api/v1/spool"  # no '//api'
    assert with_key.headers["x-api-key"] == "secret"
    assert "x-api-key" not in without_key.headers


@pytest.mark.parametrize("call", [
    lambda: spoolman_client.fetch_filaments(URL),
    lambda: spoolman_client.fetch_spools(URL),
    lambda: spoolman_client.fetch_filament(URL, None, 1),
    lambda: spoolman_client.test_connection(URL),
])
async def test_reads_raise_on_http_errors_and_timeouts(spoolman_upstream, call):
    spoolman_upstream.handler = lambda request: httpx.Response(503, text="down")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await call()
    assert exc.value.response.status_code == 503

    def _timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)
    spoolman_upstream.handler = _timeout
    with pytest.raises(httpx.ConnectTimeout):
        await call()


