from __future__ import annotations

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from themis_mcp.client import ThemisClient, ThemisError
from themis_mcp.server import build_server, build_tools

ALL = {"fleet:read", "queue:read", "jobs:read", "jobs:write", "files:read", "printers:read", "printers:control"}


async def names(server) -> set[str]:
    return {t.name for t in await server.list_tools()}


async def call(server, name: str, **args) -> str:
    result = await server.call_tool(name, args)
    return result.content[0].text if result.content[0].type == "text" else result.content[0]


# ---- which tools are offered ------------------------------------------------------------------

async def test_a_full_scope_key_gets_every_tool(api):
    assert await names(build_server(api, ALL)) == set(build_tools(api))


async def test_unknown_scopes_offer_everything_themis_still_enforces_them(api):
    assert await names(build_server(api, None)) == set(build_tools(api))


async def test_a_read_only_key_gets_only_the_read_tools_it_can_use(api):
    server = build_server(api, {"fleet:read", "queue:read"})
    assert await names(server) == {"fleet_status", "list_queue"}


async def test_a_tool_needing_two_scopes_requires_both(api):
    base = {"jobs:write"}                                     # add_job also needs printers:read
    assert "add_job" not in await names(build_server(api, base))
    assert "add_job" in await names(build_server(api, base | {"printers:read"}))


async def test_read_only_mode_drops_every_control_tool_even_with_full_scopes(api):
    offered = await names(build_server(api, ALL, read_only=True))
    assert offered == {"fleet_status", "list_queue", "get_job", "list_library_files", "printer_snapshot"}


async def test_read_only_mode_can_come_from_the_environment(api, monkeypatch):
    monkeypatch.setenv("THEMIS_MCP_READ_ONLY", "1")
    assert "pause_printer" not in await names(build_server(api, ALL))


async def test_destructive_tools_are_annotated_so_clients_can_warn(api):
    by_name = {t.name: t for t in await build_server(api, ALL).list_tools()}
    assert by_name["stop_printer"].annotations.destructive_hint is True
    assert by_name["cancel_job"].annotations.destructive_hint is True
    assert by_name["fleet_status"].annotations.read_only_hint is True
    assert by_name["pause_printer"].annotations.destructive_hint is False


# ---- read tools -------------------------------------------------------------------------------

FLEET = [
    {"id": 1, "name": "Forge", "connected": True, "state": "RUNNING", "progress": 42.4, "remaining_time": 95,
     "current_print": "7", "awaiting_plate_clear": True, "queue_on": True},
    {"id": 2, "name": "Idle One", "connected": True, "state": "IDLE", "progress": 0, "remaining_time": 0, "queue_on": False},
    {"id": 3, "name": "Dark", "connected": False, "state": "unknown", "progress": 0, "remaining_time": 0},
]


async def test_fleet_status_summarises_every_printer(api, fake):
    fake.route("GET", "/fleet", FLEET)

    text = await call(build_server(api, ALL), "fleet_status")

    assert text.splitlines() == [
        "#1 Forge: running, 42% done, 1h35m left (job 7) — waiting for the plate to be cleared (mark_plate_cleared)",
        "#2 Idle One: idle [queue off]",
        "#3 Dark: offline",
    ]


async def test_fleet_status_with_no_printers(api, fake):
    fake.route("GET", "/fleet", [])
    assert await call(build_server(api, ALL), "fleet_status") == "No printers are set up."


async def test_list_queue_shows_status_printer_and_blocked_reason(api, fake):
    fake.route("GET", "/queue", [
        {"id": 7, "status": "printing", "file_name": "benchy.3mf", "plate_number": 1, "assigned_printer_id": 1},
        {"id": 9, "status": "blocked", "uploaded_file_id": 4, "plate_number": 2, "block_reason": "No eligible printer"},
    ])
    assert (await call(build_server(api, ALL), "list_queue")).splitlines() == [
        "job 7 [printing] benchy.3mf plate 1 on printer 1",
        "job 9 [blocked] file 4 plate 2 — No eligible printer",
    ]


async def test_get_job_includes_estimate_and_slice_failures(api, fake):
    fake.route("GET", "/jobs/9/details", {
        "id": 9, "status": "blocked", "uploaded_file_id": 4, "plate_number": 1, "block_reason": "slice failed",
        "file": {"id": 4, "original_filename": "vase.3mf"}, "estimate_seconds": 5400, "estimate_filament_grams": 21,
        "printer_configs": [{"printer_name": "Forge", "slice_failed": True, "slice_error": "bad mesh"},
                            {"printer_name": "Other", "slice_failed": False}],
    })
    assert (await call(build_server(api, ALL), "get_job", job_id=9)).splitlines() == [
        "job 9 [blocked] vase.3mf plate 1 — slice failed", "estimate 1h30m, 21 g", "slice failed on Forge: bad mesh"]


async def test_list_library_files_passes_the_search_and_caps_the_list(api, fake):
    seen = {}
    fake.route("GET", "/files", lambda r: seen.update(q=dict(r.url.params)) or
               [{"id": i, "original_filename": f"f{i}.3mf", "plate_count": 1} for i in range(150)])
    text = await call(build_server(api, ALL), "list_library_files", search="vase")
    assert seen["q"] == {"search": "vase"}
    assert len(text.splitlines()) == 100 and text.splitlines()[0] == "file 0: f0.3mf (1 plates)"


async def test_printer_snapshot_returns_the_jpeg_as_an_image(api, fake):
    fake.route("GET", "/printers/3/snapshot", b"\xff\xd8\xff-jpeg-bytes")
    result = await build_server(api, ALL).call_tool("printer_snapshot", {"printer_id": 3})
    img = result.content[0]
    assert (img.type, img.mime_type) == ("image", "image/jpeg")


# ---- control tools ----------------------------------------------------------------------------

@pytest.mark.parametrize("tool, path, message", [
    ("pause_printer", "/printers/4/pause", "Paused printer 4."),
    ("resume_printer", "/printers/4/resume", "Resumed printer 4."),
    ("mark_plate_cleared", "/printers/4/plate-cleared", "Printer 4 is marked ready for new work."),
])
async def test_simple_control_tools_post_to_the_matching_route(api, fake, tool, path, message):
    fake.route("POST", path, {})
    assert await call(build_server(api, ALL), tool, printer_id=4) == message
    assert fake.posts() == [path]


# ---- destructive tools need confirmation ------------------------------------------------------

async def test_stop_printer_only_describes_until_confirmed(api, fake):
    fake.route("GET", "/fleet", FLEET)
    fake.route("POST", "/printers/1/stop", {})
    server = build_server(api, ALL)

    preview = await call(server, "stop_printer", printer_id=1)

    assert preview.startswith("NOT stopped yet.") and "Forge" in preview and "confirm=true" in preview
    assert fake.posts() == []                                   # nothing was stopped

    assert await call(server, "stop_printer", printer_id=1, confirm=True) == "Stopped printer 1."
    assert fake.posts() == ["/printers/1/stop"]


async def test_cancel_job_only_describes_until_confirmed(api, fake):
    fake.route("GET", "/jobs/7/details", {"id": 7, "status": "printing", "uploaded_file_id": 1, "plate_number": 1,
                                          "file": {"original_filename": "benchy.3mf"}, "assigned_printer_id": 1})
    fake.route("POST", "/jobs/7/cancel", {})
    server = build_server(api, ALL)

    preview = await call(server, "cancel_job", job_id=7)
    assert preview.startswith("NOT cancelled yet.") and "benchy.3mf" in preview and fake.posts() == []

    assert await call(server, "cancel_job", job_id=7, confirm=True) == "Cancelled job 7."
    assert fake.posts() == ["/jobs/7/cancel"]


@pytest.mark.parametrize("confirm", [False, "false", "False", 0, "0", "no", None, ""])
async def test_only_a_real_true_confirms_whatever_the_client_sends(api, fake, confirm):
    """The MCP layer validates/coerces arguments before the tool runs; nothing falsy-looking may reach the POST."""
    fake.route("GET", "/jobs/7/details", {"id": 7, "status": "queued", "uploaded_file_id": 1})
    fake.route("GET", "/fleet", FLEET)
    fake.route("POST", "/jobs/7/cancel", {})
    fake.route("POST", "/printers/1/stop", {})
    server = build_server(api, ALL)

    for tool, args in (("cancel_job", {"job_id": 7}), ("stop_printer", {"printer_id": 1})):
        try:
            await server.call_tool(tool, {**args, "confirm": confirm})
        except ToolError:
            pass                                    # rejected by validation (e.g. None): equally fine
    assert fake.posts() == []


async def test_the_preview_needs_read_scopes_so_those_are_required_to_offer_the_destructive_tools(api):
    only_control = {"printers:control", "jobs:write"}
    assert not {"stop_printer", "cancel_job"} & await names(build_server(api, only_control))
    assert {"stop_printer", "cancel_job"} <= await names(build_server(api, only_control | {"fleet:read", "jobs:read"}))


# ---- add_job ----------------------------------------------------------------------------------

async def test_add_job_uses_the_printers_first_profile_by_default(api, fake):
    fake.route("GET", "/printers/2/profiles", {"print_profiles": ["0.20mm Standard", "0.08 Fine"]})
    fake.route("POST", "/jobs", lambda r: {"id": 31})

    text = await call(build_server(api, ALL), "add_job", file_id=5, printer_id=2, plate_number=2)

    assert text == "Queued job 31: file 5 plate 2 on printer 2 (0.20mm Standard)."
    import json
    body = json.loads(next(r for r in fake.requests if r.method == "POST").content)
    assert body == {"uploaded_file_id": 5, "plate_number": 2, "printer_configs": [
        {"printer_id": 2, "print_profile": "0.20mm Standard", "filament_type": "any", "filament_color": "any"}]}


async def test_add_job_with_an_explicit_profile_does_not_look_one_up(api, fake):
    fake.route("POST", "/jobs", {"id": 32})
    await call(build_server(api, ALL), "add_job", file_id=5, printer_id=2, print_profile="Custom")
    assert fake.calls() == [("POST", "/jobs")]


async def test_add_job_explains_when_the_printer_has_no_profiles(api, fake):
    fake.route("GET", "/printers/2/profiles", {"print_profiles": []})
    with pytest.raises(ToolError, match="no print profiles"):
        await call(build_server(api, ALL), "add_job", file_id=5, printer_id=2)
    assert fake.posts() == []


# ---- errors and the client --------------------------------------------------------------------

async def test_a_missing_scope_is_reported_in_plain_words(api, fake):
    fake.route("POST", "/printers/1/pause", httpx.Response(403, json={"detail": "API key lacks required scope: printers:control"}))
    with pytest.raises(ToolError, match="Not allowed: API key lacks required scope: printers:control"):
        await call(build_server(api, ALL), "pause_printer", printer_id=1)


@pytest.mark.parametrize("status, expected", [
    (401, "rejected the API key"),
    (409, "Themis returned 409: already stopping"),
    (422, "Themis returned 422: Field required"),
])
async def test_other_http_errors_become_clear_tool_errors(api, fake, status, expected):
    detail = [{"msg": "Field required"}] if status == 422 else "already stopping"
    fake.route("POST", "/printers/1/resume", httpx.Response(status, json={"detail": detail}))
    with pytest.raises(ToolError, match=expected):
        await call(build_server(api, ALL), "resume_printer", printer_id=1)


async def test_an_unreachable_themis_is_reported(fake):
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    api = ThemisClient("http://themis.test", "k", transport=httpx.MockTransport(boom))
    with pytest.raises(ToolError, match="Could not reach Themis"):
        await call(build_server(api, ALL), "fleet_status")


async def test_every_request_carries_the_api_key(api, fake):
    fake.route("GET", "/fleet", [])
    await call(build_server(api, ALL), "fleet_status")
    assert fake.requests[0].headers["x-api-key"] == "thm_secret"


async def test_no_key_means_no_header(fake):
    fake.route("GET", "/fleet", [])
    keyless = ThemisClient("http://themis.test", None, transport=httpx.MockTransport(fake))
    await keyless.json("GET", "/fleet")
    assert "x-api-key" not in fake.requests[0].headers


@pytest.mark.parametrize("response, expected", [
    ({"role": "staff", "scopes": ["queue:read", "fleet:read"]}, {"queue:read", "fleet:read"}),
    ({"role": "staff"}, None),                                   # an older Themis that doesn't report scopes
    ({"role": "staff", "scopes": "oops"}, None),
    (httpx.Response(401, json={"detail": "nope"}), None),
])
async def test_scopes_probe(api, fake, response, expected):
    fake.route("GET", "/auth/me", response)
    assert await api.scopes() == expected


async def test_a_url_that_is_not_themis_is_reported_not_crashed_on(fake):
    fake.route("GET", "/fleet", httpx.Response(200, text="<html>not the API</html>", headers={"content-type": "text/html"}))
    fake.route("GET", "/auth/me", httpx.Response(200, text="<html>spa</html>", headers={"content-type": "text/html"}))
    api = ThemisClient("http://themis.test", "k", transport=httpx.MockTransport(fake))

    assert await api.scopes() is None                                   # degrades (offer everything) instead of crashing
    with pytest.raises(ToolError, match="did not answer like Themis"):
        await call(build_server(api, ALL), "fleet_status")


def test_from_env_reads_url_and_key(monkeypatch):
    monkeypatch.setenv("THEMIS_URL", "http://farm.lan:8000/")
    monkeypatch.setenv("THEMIS_API_KEY", "thm_abc")
    c = ThemisClient.from_env()
    assert str(c._http.base_url) == "http://farm.lan:8000"
    assert c._http.headers["x-api-key"] == "thm_abc"
