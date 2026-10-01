"""Builds the MCP server: read tools, control tools, and confirmation for destructive ones.

Every tool declares the Themis API scopes it needs. At startup the server asks Themis which scopes the
configured key has and registers only the tools that key can use (so a read-only key gets a read-only
assistant); Themis itself still enforces scopes on every call, so this is convenience, not the security
boundary."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import ThemisClient, ThemisError

INSTRUCTIONS = (
    "Tools for a Themis 3D-print-farm manager. Start with fleet_status or list_queue to see what is happening. "
    "Destructive tools (stop_printer, cancel_job) only describe what they would do until called again with "
    "confirm=true — always show that description to the user and get their explicit go-ahead first."
)


def _mins(seconds: float | int | None) -> str:
    if not seconds:
        return "?"
    m = int(round(seconds / 60))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def _fmt_printer(p: dict) -> str:
    state = "offline" if not p.get("connected") else str(p.get("state", "unknown")).lower()
    line = f"#{p['id']} {p['name']}: {state}"
    if state in ("running", "pause", "paused"):
        line += f", {p.get('progress', 0):.0f}% done, {_mins(p.get('remaining_time', 0) * 60)} left"
        if p.get("current_print"):
            line += f" (job {p['current_print']})"
    if p.get("awaiting_plate_clear"):
        line += " — waiting for the plate to be cleared (mark_plate_cleared)"
    if p.get("queue_on") is False:
        line += " [queue off]"
    return line


def _fmt_job(j: dict) -> str:
    name = j.get("file_name") or f"file {j.get('uploaded_file_id')}"
    line = f"job {j['id']} [{j['status']}] {name} plate {j.get('plate_number', 1)}"
    if j.get("assigned_printer_id") is not None:
        line += f" on printer {j['assigned_printer_id']}"
    if j.get("block_reason"):
        line += f" — {j['block_reason']}"
    return line


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., Awaitable]
    scopes: frozenset[str]
    annotations: ToolAnnotations
    control: bool = False   # changes something (dropped in read-only mode)


READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def build_tools(api: ThemisClient) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}

    def tool(name: str, scopes: set[str], annotations: ToolAnnotations, control: bool = False):
        def deco(fn):
            tools[name] = Tool(fn, frozenset(scopes), annotations, control)
            return fn
        return deco

    # ---- read ---------------------------------------------------------------------------------

    @tool("fleet_status", {"fleet:read"}, READ)
    async def fleet_status() -> str:
        """Every printer's live state: idle/printing/paused/offline, progress, time left, and whether it is waiting for its plate to be cleared."""
        printers = await api.json("GET", "/fleet")
        return "\n".join(_fmt_printer(p) for p in printers) or "No printers are set up."

    @tool("list_queue", {"queue:read"}, READ)
    async def list_queue() -> str:
        """The active job queue in order: queued, slicing, printing, paused, blocked and failed jobs, with why a job is blocked."""
        jobs = await api.json("GET", "/queue")
        return "\n".join(_fmt_job(j) for j in jobs) or "The queue is empty."

    @tool("get_job", {"jobs:read"}, READ)
    async def get_job(job_id: int) -> str:
        """Details of one job: file, plate, status, printer, estimate, and any failure/blocked reason."""
        d = await api.json("GET", f"/jobs/{job_id}/details")
        parts = [_fmt_job({**d, "file_name": (d.get("file") or {}).get("original_filename")})]
        if d.get("estimate_seconds"):
            parts.append(f"estimate {_mins(d['estimate_seconds'])}, {d.get('estimate_filament_grams') or '?'} g")
        for cfg in d.get("printer_configs") or []:
            if cfg.get("slice_failed"):
                parts.append(f"slice failed on {cfg.get('printer_name')}: {cfg.get('slice_error') or 'unknown error'}")
        return "\n".join(parts)

    @tool("list_library_files", {"files:read"}, READ)
    async def list_library_files(search: str | None = None) -> str:
        """Files in the model library (id, name, plate count), optionally filtered by a name search. Use the id with add_job."""
        files = await api.json("GET", "/files", params={"search": search} if search else None)
        return "\n".join(f"file {f['id']}: {f.get('original_filename')} ({f.get('plate_count', '?')} plates)"
                         for f in files[:100]) or "No matching files."

    @tool("printer_snapshot", {"printers:read"}, READ)
    async def printer_snapshot(printer_id: int) -> Image:
        """A current camera picture from a printer (JPEG)."""
        return Image(data=await api.bytes(f"/printers/{printer_id}/snapshot"), format="jpeg")

    # ---- control ------------------------------------------------------------------------------

    @tool("pause_printer", {"printers:control"}, WRITE, control=True)
    async def pause_printer(printer_id: int) -> str:
        """Pause the print running on a printer (it can be resumed)."""
        await api.json("POST", f"/printers/{printer_id}/pause")
        return f"Paused printer {printer_id}."

    @tool("resume_printer", {"printers:control"}, WRITE, control=True)
    async def resume_printer(printer_id: int) -> str:
        """Resume a paused print."""
        await api.json("POST", f"/printers/{printer_id}/resume")
        return f"Resumed printer {printer_id}."

    @tool("mark_plate_cleared", {"printers:control"}, WRITE, control=True)
    async def mark_plate_cleared(printer_id: int) -> str:
        """Tell Themis the printer's plate has been physically cleared, so the queue may start the next job on it. Only call after the user confirms the plate is clear."""
        await api.json("POST", f"/printers/{printer_id}/plate-cleared")
        return f"Printer {printer_id} is marked ready for new work."

    @tool("stop_printer", {"printers:control"}, DESTRUCTIVE, control=True)
    async def stop_printer(printer_id: int, confirm: bool = False) -> str:
        """DESTRUCTIVE: stop (abort) the running print, which cannot be resumed. Without confirm=true this only describes what would be stopped; show that to the user and call again with confirm=true once they agree."""
        if not confirm:
            printers = await api.json("GET", "/fleet")
            p = next((x for x in printers if x.get("id") == printer_id), None)
            what = _fmt_printer(p) if p else f"printer {printer_id}"
            return (f"NOT stopped yet. This would abort the print on: {what}. "
                    f"A stopped print cannot be resumed. Call stop_printer again with confirm=true to proceed.")
        await api.json("POST", f"/printers/{printer_id}/stop")
        return f"Stopped printer {printer_id}."

    @tool("cancel_job", {"jobs:write"}, DESTRUCTIVE, control=True)
    async def cancel_job(job_id: int, confirm: bool = False) -> str:
        """DESTRUCTIVE: cancel a job (stops its printer if it is printing). Without confirm=true this only describes the job; show that to the user and call again with confirm=true once they agree."""
        if not confirm:
            d = await api.json("GET", f"/jobs/{job_id}/details")
            return (f"NOT cancelled yet. This would cancel: {_fmt_job({**d, 'file_name': (d.get('file') or {}).get('original_filename')})}. "
                    f"Call cancel_job again with confirm=true to proceed.")
        await api.json("POST", f"/jobs/{job_id}/cancel")
        return f"Cancelled job {job_id}."

    @tool("add_job", {"jobs:write", "printers:read"}, WRITE, control=True)
    async def add_job(file_id: int, printer_id: int, plate_number: int = 1, print_profile: str | None = None) -> str:
        """Queue a library file for printing on a printer. print_profile defaults to the printer's first available profile; filament is 'any'."""
        if print_profile is None:
            profiles = (await api.json("GET", f"/printers/{printer_id}/profiles")).get("print_profiles") or []
            if not profiles:
                raise ThemisError(f"Printer {printer_id} has no print profiles to slice with; pass print_profile.")
            print_profile = profiles[0]
        job = await api.json("POST", "/jobs", json={
            "uploaded_file_id": file_id, "plate_number": plate_number,
            "printer_configs": [{"printer_id": printer_id, "print_profile": print_profile,
                                 "filament_type": "any", "filament_color": "any"}],
        })
        return f"Queued job {job['id']}: file {file_id} plate {plate_number} on printer {printer_id} ({print_profile})."

    return tools


def build_server(api: ThemisClient, granted: set[str] | None = None, *, read_only: bool | None = None) -> MCPServer:
    """Register the tools `granted` (the key's scopes; None = unknown, offer everything) allows.
    `read_only` (default: THEMIS_MCP_READ_ONLY) additionally drops every control tool."""
    if read_only is None:
        read_only = os.environ.get("THEMIS_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")
    server = MCPServer("themis", instructions=INSTRUCTIONS)
    for name, t in build_tools(api).items():
        if read_only and t.control:
            continue
        if granted is not None and not t.scopes <= granted:
            continue
        server.add_tool(_safe(t.fn), name=name, description=t.fn.__doc__, annotations=t.annotations)
    return server


def _safe(fn):
    """Turn Themis failures into a tool error carrying the message (any other exception reaches the client without it)."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except ThemisError as e:
            raise ToolError(str(e)) from None
    return wrapper
