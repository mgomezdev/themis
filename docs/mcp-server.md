# Themis MCP server (Farm Agent)

`mcp-server/` is an [MCP](https://modelcontextprotocol.io) server that exposes Themis's REST API as tools, so an AI
assistant — Claude Desktop, Claude Code, any MCP client — can answer "what's printing?", show a camera snapshot,
pause a print or queue a file. Themis doesn't run an LLM; you bring the assistant.

## Install and connect

```bash
pip install -e mcp-server          # from the repo root (Python ≥ 3.10)
```

1. In Themis → **Settings → API Keys**, create a key. Give it only the scopes the assistant should have (below).
2. Point your MCP client at the `themis-mcp` command with the key in its environment.

**Claude Code**

```bash
claude mcp add themis -e THEMIS_URL=http://localhost:8001 -e THEMIS_API_KEY=thm_… -- themis-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "themis": {
      "command": "themis-mcp",
      "env": { "THEMIS_URL": "http://localhost:8001", "THEMIS_API_KEY": "thm_…" }
    }
  }
}
```

| Variable | Meaning |
|---|---|
| `THEMIS_URL` | Base URL of Themis (default `http://localhost:8001`; the Docker image listens on `:8000`). |
| `THEMIS_API_KEY` | The API key. May be omitted when Themis runs on a trusted local network that allows keyless admin access. |
| `THEMIS_MCP_READ_ONLY` | `1` offers only the read tools, whatever the key can do. |

## Tools and the scopes they need

At startup the server asks Themis (`GET /api/v1/auth/me`) which scopes the key has and **offers only the tools that key
can use** — a read-only key gives a read-only assistant. This is a convenience: Themis enforces scopes on every call
regardless. Against a Themis too old to report scopes, every tool is offered and a missing scope surfaces as a clear
"Not allowed … lacks required scope" message.

| Tool | Does | Scopes |
|---|---|---|
| `fleet_status` | Every printer's state, progress, time left, plate-clear wait | `fleet:read` |
| `list_queue` | Active queue in order, with why a job is blocked | `queue:read` |
| `get_job` | One job's file, status, printer, estimate, slice failures | `jobs:read` |
| `list_library_files` | Library files (id, name, plates), optional name search | `files:read` |
| `printer_snapshot` | A current camera JPEG | `printers:read` |
| `pause_printer`, `resume_printer` | Pause / resume a print | `printers:control` |
| `mark_plate_cleared` | "Ready for new work" after the plate is physically cleared | `printers:control` |
| `add_job` | Queue a library file on a printer (first print profile unless one is given) | `jobs:write`, `printers:read` |
| `stop_printer` ⚠ | Abort a running print | `printers:control`, `fleet:read` (for the preview) |
| `cancel_job` ⚠ | Cancel a job (stops its printer if printing) | `jobs:write`, `jobs:read` (for the preview) |

## Destructive actions need confirmation

`stop_printer` and `cancel_job` do nothing on the first call: they return a description of exactly what would be stopped
or cancelled and tell the assistant to ask you. Only a second call with `confirm=true` acts. They are also annotated
`destructiveHint`, so MCP clients that show tool warnings will. `pause_printer`/`mark_plate_cleared` are reversible or
bookkeeping and act immediately; use a key without `printers:control` (or `THEMIS_MCP_READ_ONLY=1`) to take them away.

## Not included (yet)

- **Chat bridge** — see [discord-bridge-spike.md](discord-bridge-spike.md) for the design and what is needed.
- Transport is stdio (a local process per client). Running it remotely would mean the MCP streamable-HTTP transport plus
  authentication in front of it; not done.
- Tool output is text. `add_job` doesn't pick filaments (it asks for "any").

## Development

```bash
cd mcp-server && pip install -e ".[dev]" && pytest
```

Tests drive the real MCP server in-process against a fake Themis (`httpx.MockTransport`). The server targets
`mcp` 2.x (`MCPServer`, renamed from `FastMCP` in 1.x).
