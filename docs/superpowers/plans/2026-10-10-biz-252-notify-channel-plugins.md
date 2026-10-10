# BIZ-252 — notification channels behind multi-active plugins

Epic BIZ-266. Contract and how to write a channel: `docs/plugins.md` § "Notification channels".

## Approach
1. `notify.channel` — a core **fan-out** capability (`plugins/capabilities/notify_channel.py`): neutral `ChannelMessage`, `ChannelResult`,
   `FilteredChannel` (the `events` allow-list), `NOTIFY_EVENTS`. Every enabled channel gets each notification, contained on its own.
2. `services/notify.py` replaces `notification_service.dispatch`: build one message, `plugin_host.fan_out`, redacted logs, failure shown as the
   plugin's `last_error`, repeated `message_id` dropped (a redelivered `job.complete` notifies once). No ntfy/Discord/SMTP code remains in core.
3. ntfy, Discord and email become bundled plugins (`plugins/notify_*`), default-enabled, secrets (Discord webhook URL, SMTP password) write-only.
   The host now creates the config row of a default-enabled provider so its instance is built.
4. Migration v048 copies the legacy `notification_config` into the plugins' `plugin_configs` (enabled flag, settings, events, secrets).
5. `/settings/notifications*` and the Notifications page are removed; the channels' own pages appear in the Settings sidebar from their plugin
   UI contributions (only while enabled). `PluginSettingsPage` gains checkbox/comma-list widgets for array settings and drops the "Use for"
   choice for fan-out/routed capabilities.

## Tests
`tests/notify/` (senders, fan-out with fake third-party channels, independence/timeout/disable/uninstall, dedupe, redaction, health, test button,
v048, bundled end to end), updated alarms/inventory/queue-engine/golden tests, `PluginSettingsPage` widgets; full backend + frontend + Playwright run.

## Not in scope
Retrying a failed notification (none is retried today); a UI for webhook destinations (BIZ-172 left it API-only).
