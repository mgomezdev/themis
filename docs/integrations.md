# Integrations and API

The current REST contract is available from the running instance at `/openapi.json` and `/docs`; the repository copy is [`openapi.json`](../openapi.json). Routes are rooted at `/api/v1`. Treat the generated OpenAPI schema as authoritative for paths, request bodies, and response fields. The browser live-update socket is `/ws`.

## API authentication

Send staff or integration credentials in the `X-Api-Key` header. Keys are created in Settings → API Keys and can be scoped and revoked. `GET /api/v1/auth/me` reports the authenticated identity and scopes. Read-only integrations should receive only read scopes. The API key's scopes are enforced on every route; a UI or client hiding unavailable actions is not an authorization boundary.

For end-to-end project creation, idempotent generation, and signed webhook examples, see the [companion application guide](companion-apps.md).

Keyless local administrator access is controlled by the administrator setting and `THEMIS_LOCAL_NETWORKS`; see [Installation and administration](operations.md#authentication-and-account-recovery). Customer login sessions are restricted to `/api/v1/customer/*` and a single customer. The public project-share endpoint is token-based. Avoid putting keys in URLs; query credentials are accepted only for image endpoints that cannot set headers (thumbnails, camera snapshots, and camera streams).

Scopes currently declared by the server include `files:read/write`, `jobs:read/write`, `printers:read/write/control`, `queue:read/write`, `fleet:read`, `orders:read/write`, `projects:read/write/share`, `laminus:read/write`, `settings:read/write`, `inventory:read/write`, `tags:read/write`, `maintenance:read/write`, `apikeys:read/write`, and `customers:read/write`. The `customer` scope is reserved for customer sessions. Some actions additionally require an interactive administrator session; a scoped API key cannot install code or perform those instance-level operations. Verify exact per-route scopes in OpenAPI and in the current Settings UI.

## Webhooks and live events

The app supports configured outgoing event webhooks and notification channels. Configure them in Settings; event selection and signing-secret behavior are exposed by the settings API and UI. Webhook delivery is asynchronous and retried according to the implementation. Do not assume delivery is synchronous with the originating job/printer event.

The `/ws` socket carries authorized live updates for the browser and integrations. Authenticate with a key using the socket's supported query parameter when a WebSocket client cannot set the normal header. The socket's event payloads are defined by the backend eventing registry; use those definitions rather than assuming every internal event is broadcast.

## Plugin integrations

Printer vendors and optional features are plugin-managed. Plugins declare printer models and capabilities; the core UI and API use those declarations to determine available controls. The system includes bundled plugins and supports managed plugin installation through the app. Plugin identity and model declarations are persisted so existing printers can be represented as dormant if a plugin or model is unavailable.

Filament inventory is exposed through the `inventory.filament` capability. Providers may advertise optional capabilities such as weight tracking, profile links, material/spool management, and label scanning. Integrations should inspect the capability/provider endpoints and handle `409` `capability_unavailable` responses instead of assuming a specific provider is installed. Legacy Spoolman endpoints may remain for compatibility; new integrations should use the provider-neutral inventory contract where available.

Slicing uses the configured Laminus provider. The sidecar must be reachable and have the required machine, process, and filament profiles. Themis stores provider references as opaque values; integrations should use the IDs returned by the API rather than constructing them.

## MCP server

`mcp-server/` is a separate Python package exposing selected Themis operations over MCP stdio. Install with `pip install -e mcp-server`, configure `THEMIS_URL` and `THEMIS_API_KEY`, and launch `themis-mcp` from the MCP client. `THEMIS_MCP_READ_ONLY=1` removes control tools. The server probes the key's scopes and offers only tools the key can use; Themis still enforces the scopes itself.

Read tools report fleet status, queue, job details, library files, and printer snapshots. Control tools can pause/resume printers, mark a plate cleared, and add a job. `stop_printer` and `cancel_job` first return a preview; the client must obtain explicit user confirmation before calling again with `confirm=true`. The MCP package currently uses stdio; it does not expose remote HTTP transport. See [MCP server setup and tool details](mcp-server.md).

## Updating the API contract

When changing routes or schemas, update the backend route and client as needed, regenerate the schema, and keep the API response-key contract current:

```sh
python scripts/export_openapi.py
```

`contracts/response-keys.json` tracks response keys read by the frontend. Update it when those consumed keys change. The repository's backend and frontend contract checks cover schema drift; see [Contributing](../CONTRIBUTING.md).
