# Documentation index

Use the current guides for supported behavior. The application source and generated OpenAPI schema are authoritative when a code detail changes.

## Using and operating Themis

- [Operator guide](user-guide.md): jobs, printers, files, projects, customers, inventory, and other screens.
- [Installation and administration](operations.md): Docker Compose, local development, settings, authentication, recovery, and backups.
- [Integrations and API](integrations.md): API keys/scopes, events, webhooks, plugins, and MCP.
- [Companion application guide](companion-apps.md): retry-safe project creation/generation and signed project/job webhooks.
- [MCP server](mcp-server.md): install and configure the optional stdio integration.

## Developer and maintainer references

- [Contributor guide](../CONTRIBUTING.md): development setup, repository map, and change guidance.
- [Architecture index](agent/README.md): backend, frontend, data model, printer, provider, event, and plugin references.
- [Plugin guide](plugins.md): plugin package, capability, install, and lifecycle contracts. [Plugin data lifecycle](plugin-lifecycle.md): ownership, retention and recovery. [Plugin test matrix](plugin-test-matrix.md): scenario-to-test map.
- [Printer discovery](printer-discovery.md) and [printer interface](printer-interface.md).
- [Provider interfaces](provider-interfaces.md) and [slicing flow](slicing-flow.md).
- [Event contract](events.md) and [protocol verification](../backend/protocol_verification/README.md).

## Historical records

`superpowers/specs/`, `superpowers/plans/`, and `superpowers/handoffs/` are dated design and implementation records. They can document rejected or superseded approaches; they are not statements of current product behavior. Dated reviews and spike documents elsewhere under `docs/` are also historical unless they explicitly link to a current reference.
