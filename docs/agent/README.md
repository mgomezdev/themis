# Current architecture references

These references describe the implementation in this checkout. For operator-facing material, start with the [user guide](../user-guide.md), [operations guide](../operations.md), and [integrations guide](../integrations.md). For exact HTTP paths and schemas, use the running `/docs` or the committed [`openapi.json`](../../openapi.json).

| Document | Use it for |
|---|---|
| [backend.md](backend.md) | Backend routes, services, queue and slicing flow, and major code paths. Check OpenAPI for the exact live route contract. |
| [frontend.md](frontend.md) | Browser routes, screens, API clients, and UI architecture. |
| [data-model.md](data-model.md) | Core database tables, relationships, and migrations. Plugin-owned tables are plugin-specific. |
| [printers.md](printers.md) and [printer-interface.md](../printer-interface.md) | Printer plugin/model declarations, client boundary, protocol behavior, and capabilities. |
| [provider-interfaces.md](../provider-interfaces.md) | Slicing and filament-inventory provider contracts and adapter boundaries. |
| [conventions.md](conventions.md) | Implementation invariants and local development gotchas. |
| [recipes.md](recipes.md) | Common maintenance and implementation tasks. |
| [styling.md](styling.md) | Frontend styling and design tokens. |
| [backend-review.md](backend-review.md), [frontend-review.md](frontend-review.md) | Change-review checklists. |
| [events.md](../events.md) | Event contracts, durability, subscribers, and operator endpoints. |
| [plugins.md](../plugins.md) | Plugin manifests, capabilities, lifecycle, and installation behavior. |
| [mcp-server.md](../mcp-server.md) | Optional MCP package setup and tools. |

These are maintained architecture notes, not generated specifications. When details differ, current source code is authoritative; for REST request/response contracts, OpenAPI is authoritative. The dated design specs, implementation plans, and handoffs under `docs/superpowers/` are historical records and may describe behavior that was changed or never shipped.
