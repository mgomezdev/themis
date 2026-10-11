# Themis MCP server

`mcp-server/` is a separate MCP stdio server that calls Themis's REST API. It lets an MCP-compatible assistant inspect a farm and perform selected actions. Themis does not provide the assistant or model.

## Install and connect

Install the package from the repository root:

```sh
pip install -e mcp-server
```

Create a scoped API key in Themis → Settings → API Keys, then configure the MCP client to launch `themis-mcp` with the server URL and key.

Claude Code:

```sh
claude mcp add themis -e THEMIS_URL=http://localhost:8001 -e THEMIS_API_KEY=thm_… -- themis-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "themis": {
      "command": "themis-mcp",
      "env": {
        "THEMIS_URL": "http://localhost:8001",
        "THEMIS_API_KEY": "thm_your_key"
      }
    }
  }
}
```

| Variable | Meaning |
|---|---|
| `THEMIS_URL` | Base URL of Themis. Defaults to `http://localhost:8001`. |
| `THEMIS_API_KEY` | The scoped API key. Keyless local admin access can work on a trusted local installation, but a scoped key is recommended for MCP. |
| `THEMIS_MCP_READ_ONLY` | Set to `1`, `true`, or `yes` to omit all control tools. |

At startup, the server asks `GET /api/v1/auth/me` for the key's scopes and registers only tools those scopes permit. This filters the assistant's available tools; Themis still checks authorization on every request. If the server cannot determine scopes, it exposes the tool set and lets API authorization errors surface when a tool is called.

## Tools

| Tool | Purpose | Required scopes |
|---|---|---|
| `fleet_status` | Printer state, progress, time remaining, and plate-clear status. | `fleet:read` |
| `list_queue` | Active queue and job status/reason. | `queue:read` |
| `get_job` | Job details, estimates, and per-printer slice failures. | `jobs:read` |
| `list_library_files` | Search model library files. | `files:read` |
| `printer_snapshot` | Return a printer camera snapshot. | `printers:read` |
| `pause_printer`, `resume_printer` | Pause or resume printing. | `printers:control` |
| `mark_plate_cleared` | Mark a physically cleared printer ready for work. | `printers:control` |
| `add_job` | Queue a library file on a printer. Defaults to the first available process profile and filament `any`. | `jobs:write`, `printers:read` |
| `stop_printer` | Abort the active print. Requires a preview call and then `confirm=true`. | `printers:control`, `fleet:read` |
| `cancel_job` | Cancel a job, stopping its printer if it is printing. Requires a preview call and then `confirm=true`. | `jobs:write`, `jobs:read` |

The assistant must show the preview returned by `stop_printer` or `cancel_job` and obtain explicit user approval before calling again with `confirm=true`. These prints cannot be resumed after stopping. Marking a plate cleared should only be done after confirming the plate is physically clear.

## Development

```sh
cd mcp-server
pip install -e ".[dev]"
pytest
```

The package currently serves stdio only; it does not implement remote HTTP transport. `add_job` does not choose a spool or a specific filament profile. Keep tool scope declarations in sync with the server's `SCOPES` registry and routes in OpenAPI.
