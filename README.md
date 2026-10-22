# Themis

Themis is a self-hosted web app for operating a fleet of 3D printers. It manages a shared job queue, printer state, model and filament libraries, projects and orders, maintenance, and customer workflows. Printer integrations and optional features are supplied through plugins. Slicing uses the Laminus OrcaSlicer sidecar when configured.

## Start here

- [Documentation index](docs/README.md): current user, operations, integration, and developer guides.
- [Operator guide](docs/user-guide.md): queue, printers, files, projects, customers, inventory, alarms, and analytics.
- [Installation and administration](docs/operations.md): Docker deployment, configuration, authentication, backups, and recovery.
- [Integrations guide](docs/integrations.md): REST API, OpenAPI, API keys, webhooks, plugins, and the MCP server.
- [Developer guide](CONTRIBUTING.md): local setup, code layout, and change workflow.
- [Architecture references](docs/agent/README.md): current backend, frontend, data model, provider, and printer notes.

## Run with Docker Compose

The Compose deployment builds Themis and a Laminus sidecar from a sibling `../laminus` checkout. Set `APPDATA` in `.env` to the host OrcaSlicer configuration directory (Windows: `%APPDATA%`; Linux: `$HOME/.config`), then run:

```sh
docker compose up --build
```

The app listens on host port `8001` by default. Set `HOST_PORT` to change it. Persistent Themis data is stored in the `themis-data` Docker volume. See [Installation and administration](docs/operations.md) for setup requirements, local development, and configuration details.

## What the app includes

- Printer fleet management, capability-based controls, live telemetry, cameras, printer files, and alarms.
- A shared queue with eligibility checks, slicing, upload and print dispatch, job history, and slice caching.
- A model library, orders, projects, maintenance, analytics, customers, labor and payment tracking.
- Plugin-managed printer integrations and optional capabilities, including filament inventory providers.
- Scoped API keys, customer portal sessions, project share links, webhooks, and a separate stdio MCP server.

The installed plugins and their declared capabilities determine which printer models and optional features are available. A Laminus sidecar is required for operations that need a new slice; file-library and already-sliced G-code workflows can still be used without it.

## API and generated contract

The API is rooted at `/api/v1`. The running app serves interactive OpenAPI documentation at `/docs` and the schema at `/openapi.json`. [`openapi.json`](openapi.json) is the committed API contract; regenerate it after route or schema changes with:

```sh
python scripts/export_openapi.py
```

The browser UI uses a WebSocket at `/ws`. For authentication, scopes, webhooks, plugins, and MCP setup, see the [integrations guide](docs/integrations.md).

## Repository documentation

The dated design specs, implementation plans, handoffs, and reviews contain project history. They may describe superseded or unshipped behavior; use the current source code, OpenAPI schema, and guides in the [documentation index](docs/README.md) for supported behavior.
