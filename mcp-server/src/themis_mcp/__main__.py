"""`themis-mcp` — run the Themis MCP server over stdio (what Claude Desktop / Claude Code launch).

Configuration (environment): THEMIS_URL (default http://localhost:8001), THEMIS_API_KEY, and optionally
THEMIS_MCP_READ_ONLY=1 to offer only the read tools."""
from __future__ import annotations

import asyncio

from .client import ThemisClient
from .server import build_server


async def _probe_scopes() -> set[str] | None:
    api = ThemisClient.from_env()
    try:
        return await api.scopes()
    finally:
        await api.aclose()


def main() -> None:
    # Ask Themis which scopes the key has (in its own event loop, with its own client) so only usable tools
    # are offered; then serve with a fresh client inside the server's loop.
    granted = asyncio.run(_probe_scopes())
    build_server(ThemisClient.from_env(), granted).run("stdio")


if __name__ == "__main__":
    main()
