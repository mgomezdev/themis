# Spike: chat bridge (Discord / Slack) for the Farm Agent

**Status: design only — nothing here is built or tested.** BIZ-161 asked for a "Discord bridge spike". A runnable
bridge needs a Discord bot token and an LLM key, neither of which was available while building the MCP server, so this
records what the bridge would be and the decisions to make before building it.

## Goal

Message a bot ("what's printing?", "show me printer 7", "requeue the failed job") from a phone and get answers, camera
pictures and — after confirmation — actions, **without Themis running or hosting an LLM**.

## Shape

```
Discord ──▶ bridge process ──▶ LLM (user's key or a local model) ──▶ tool calls
                  │                                   ▲
                  └──── MCP client (stdio) ──▶ themis-mcp ──▶ Themis REST API (scoped key)
```

- The bridge is a small separate process (not part of the Themis container): a Discord client (`discord.py`) + an
  agent loop + an MCP client that launches `themis-mcp`. All farm access goes through the MCP tools, so **permissions
  are whatever the Themis API key allows**; a read-only key makes a read-only bot.
- Each Discord message becomes a user turn; the loop runs until the model stops calling tools; the reply (text, and
  `printer_snapshot` images as attachments) goes back to the channel.

## Decisions to make

1. **Which LLM.** Bring-your-own key (Anthropic/OpenAI-compatible) or a local model via an OpenAI-compatible endpoint
   (Ollama). Keeps Themis "no-cloud by default" — the bridge reads the key from its own environment.
2. **Who may talk to it.** Restrict to specific Discord user ids / a private channel. An open bot is an open control
   plane for the farm.
3. **Confirmation UX.** The MCP tools already refuse `stop_printer` / `cancel_job` without `confirm=true` and describe
   the action first. In Discord, render that as a confirm/cancel **button** and have only the button press (by the
   asking user) allow the second call — don't let the model assert the user agreed.
4. **Proactive messages.** Failure/complete alerts already exist as Themis webhooks and notification channels (incl.
   Discord). The bridge shouldn't duplicate them; keep it reactive.
5. **Rate / cost limits.** Cap tool-call rounds and per-user messages per minute; images are the large part.

## Risks

- Prompt injection through data the tools return (file names, job notes) steering the model into destructive calls →
  confirmation buttons (3) and a least-privilege key are the mitigations.
- A leaked bot token or an over-broad key. Use a dedicated Themis key for the bridge, revocable independently.

## Suggested first build

A ~150-line script: `discord.py` client → for each allowed user's message, a loop using the provider SDK's tool-calling
with the tool list from an in-process MCP `Client` (see `mcp-server/tests/test_server.py` for how to drive the server) →
reply. Start read-only; add control tools behind the button flow.
