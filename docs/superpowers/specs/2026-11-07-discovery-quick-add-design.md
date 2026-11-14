# Discovery quick-add & printer configuration status — design

Status: approved for planning (2026-11-07). Mockup: https://claude.ai/artifact/P3GokstvQavGvtLTeKR8h6

## Intent

Adding printers from a network scan is slow: picking a scan result opens the full add form and the scan results are
gone (`PrintersScreen.tsx` renders `DiscoverPrinters` only while no printer is picked), so adding N printers means
scanning N times. Most printers (Elegoo Centauri, Snapmaker) need nothing beyond the IP the scan already found.

Goal: add many printers from one scan, in place, in as few clicks as possible — one click per printer or one batch
click — and let incomplete setup be finished later without it ever reaching the queue.

## Goals

1. Scan results stay on screen; adding a printer updates its row in place ("Added · …"). Results persist until the
   next scan.
2. Batch add: checkbox per row, "Add N printers" bar, quick selectors (All new / Complete only / None). Per-row
   "Add" too.
3. Each printer **plugin declares which connection fields appear on the quick-add row**. Bambu: serial number and
   access code. Elegoo Centauri and Snapmaker: none.
4. A printer is defined by the **(IP, model) tuple** only; every other field is optional at creation.
5. New persisted **configured** status. Only configured printers are queue-eligible.
6. A **successful connection test** is what makes a printer configured. Every create triggers the test
   automatically; the **first successful live connection** also counts.
7. Fleet clearly distinguishes **out of queue by user choice** from **out of queue because not configured**.
8. Per-row **Test** button on quick-add rows whose plugin asks for fields.

## Non-goals

- No change to the full add form's step-by-step flow (it still works; it simply benefits from the relaxed create).
- No new discovery protocols or scan changes.
- No batch-create endpoint (see Decisions).
- No periodic background re-testing of unconfigured printers beyond the live-connection rule.

## Anti-goals (would be failures even if it "works")

- An unconfigured printer ever claiming a job.
- Core code naming a vendor or branching on a plugin id (boundary tests must stay green).
- Sending empty-string credentials to a printer (Bambu allows one LAN connection; bad logins hammer it).
- One printer's failed create/test blocking the rest of a batch.
- Existing, working fleets dropping out of the queue on upgrade.

## Decisions (made with the user)

| Topic | Decision |
|---|---|
| Identity | (IP, model) is the definition. Create requires only those two. Duplicate (IP, model) → 409. Scan's `already_added` matches on the same tuple (today: IP only). |
| Default nickname | Scanned name; fallback `<model> <last IP octet>` (e.g. "P1S 30"). |
| Quick-add fields | Plugin-declared. Missing quick-add values may be left blank and filled later. |
| Configured | Persisted boolean. True only after a successful test **or** first successful live connection. |
| Auto-test | Every create runs the test in the background. Batch add = one create per printer, tests in parallel. |
| Edit invalidates | Only a saved **change** to a connection-related field (`connection_config`: IP, serial, access code, port, …) sets `configured = false` and re-tests. Renames, model changes within the same plugin, queue/plate/rate edits, and re-saving identical values never do. A model change that moves the printer to a different plugin replaces its client, so it counts as connection-related *(Claude's call)*. |
| Model picker | Type-ahead combobox; options limited to the models of the plugin whose discovery found the IP. |
| IP conflict | When a found printer's IP belongs to an existing printer of a **different model**, the row is flagged ("192.168.7.34 is already used by A1-Bay4 (A1) — the address may have changed"), unchecked by default, with a link to edit the existing printer. Adding it as new stays possible. |
| Already-added printers | Own section on the scan, read-only, showing the saved printer's name and configured status; an **Edit** button reopens its quick-add fields inline and saves to the existing printer (PATCH). |
| Offline ≠ unconfigured | A configured printer that goes offline stays configured; offline is the live status. |
| Migration | Existing printers start `configured = true`. |
| Failed test vs add | A failed/missing test never blocks the add; the printer is saved unconfigured. |

## How it works

### Plugin-declared quick-add fields

- `ConnectionField` (`backend/app/services/abstract_printer_client.py`) gains `quick_add: bool = False`.
- Bambu plugin sets `quick_add=True` on `serial_number` and `access_code`. Elegoo, Snapmaker (Moonraker), mock: none.
- `GET /printers/types` already exposes `connection_fields`; it now includes `quick_add`. Frontend renders the
  quick-add row's inputs from that list — no vendor names in core or frontend.
- Discovery pre-fills quick-add values it learned (Bambu serial from SSDP), tagged "from scan" in the UI.

### Create (relaxed)

- `POST /printers` requires model identity (as today) and `connection_config.ip_address`. Other connection fields
  optional.
- Blank values are **omitted** from `connection_config`, never stored as `""` (frontend strips; backend also strips
  empty strings defensively).
- Uniqueness: reject (409) if a printer with the same model identity and `ip_address` exists. IP lives in the
  `connection_config` JSON, so the check is application-level in the route.
- After commit the route returns immediately with `configured: false` and schedules the auto-test (below). It does
  not block on the test.

### Configured status

- Migration `v067_printer_configured`: `printers.configured BOOLEAN NOT NULL DEFAULT 0`, plus
  `printers.config_error TEXT NULL` (last test failure / missing-field reason). Back-fill existing rows
  `configured = 1`. Register in `runner.py`.
- **Missing required fields** (a `ConnectionField` with `required=True` absent from `connection_config`): the
  printer is unconfigured with reason "Missing <label>"; no client is built and no connection attempted — at create,
  at startup (`printer_manager` startup loop logs one warning, not an exception), and after edits. Computed from the
  plugin's own field list → vendor-neutral.
- **Auto-test**: when all required fields are present, build the client and connect (same logic as
  `POST /printers/test-connection`, extracted to a shared service function). Success → `configured = true`,
  `config_error = null`. Failure → `config_error = <hint>`, split by the existing port probe
  (`_connect_failure_hint`): **unreachable** (off/asleep/wrong IP) → keep the client registered so its normal
  reconnect can produce the first live connection; **reached but login failed** → tear the client down (no retry
  storm against the printer's single LAN slot) until the user edits fields or presses Test. Runs off the request
  path (background task), so batch creates don't serialize. *(Unreachable-vs-login split: Claude's call, needed so
  the live-connection rule can fire.)*
- **First live connection**: the connectivity "online" edge already flows through
  `PrinterManager.on_state_change` → `_on_connectivity`. When an unconfigured printer's client reports connected,
  set `configured = true` and clear `config_error`. (Applies when a client exists, e.g. a printer that was asleep at
  create and whose client keeps reconnecting.)
- **Edits**: `PATCH /printers/{id}` compares the incoming `connection_config` with the stored one (after stripping
  blanks); only a real difference — or a model change to a different plugin — sets `configured = false`, then the same
  missing-field check / auto-test, and reconnect. (Today PATCH stores config but doesn't reconnect.) Name, model
  within the same plugin, and every non-connection field leave `configured` untouched.
- Response: printer dict gains `configured` and `config_error`; add both to `contracts/response-keys.json`.
- Websocket/status broadcast includes the change so Fleet and scan rows update live.

### Queue eligibility

- Eligible = `configured` AND `queue_on` AND plugin not dormant AND connected AND idle AND not `awaiting_plate_clear`.
- Enforce `configured` in `queue_engine._try_claim_for_printer` (next to the `queue_on` check) **and**
  `PrinterManager.is_printer_ready` (in-memory set of unconfigured ids, like `_awaiting_plate_clear`), so neither
  path can claim.
- Model-target materialization ("any printer of this model") skips unconfigured printers.

### Out-of-queue states in Fleet (distinct)

| State | Source | Look | Action |
|---|---|---|---|
| Queue off | user (`queue_on = false`) | grey "Queue off" | toggle back on |
| Disabled | user (`enabled = false`) | existing treatment | unchanged |
| Not configured | system (`configured = false`) | yellow "Setup incomplete" + `config_error` | quick-add fields inline + Test / Save & connect |
| Plugin dormant | system (existing) | existing treatment | unchanged |

A printer can be both queue-off and unconfigured; show unconfigured first (it's the blocking problem) and still show
the queue toggle.

### Test connection

- Reuse `POST /printers/test-connection` for unsaved rows (scan rows before Add).
- New `POST /printers/{id}/test` for saved printers: runs the shared test with the stored config; on success sets
  `configured = true` and connects the printer. Used by scan rows after Add, the Fleet "Setup incomplete" card, and
  the auto-test.
- Test button enabled only when all quick-add fields have values; editing a field clears the last result.

### Scan-results UI (`DiscoverPrinters`)

One component serves both entry points (Printers → Add/Find printers, and plugin Settings → Find printers); picking
no longer navigates away. Per the mockup:

- Rows grouped by plugin. Already-added printers (matched on the (IP, model) tuple) sit in their own "Already in
  your fleet" section: no checkbox, read-only, showing the saved printer's nickname and configured status. **Edit**
  turns its quick-add fields (and Test) back on; Save PATCHes the existing printer.
- Row: checkbox · editable nickname + model line · IP · quick-add inputs (or "Nothing to enter") + Test · status chip
  · Add.
- Status chips: Ready to add · Missing <field> · Connection OK · Test failed · Adding… · Testing… · Added ·
  configured · Added · not configured: <reason>.
- After add, rows that are unconfigured keep their inputs with Save (PATCH + re-test).
- IP conflict: the discovery response gains `ip_in_use_by: {printer_id, name, model} | null` (set when the IP matches
  an existing printer of a different model; `already_added` now matches the (IP, model) tuple). Row shows a warning
  line + "Edit <name>" link, starts unchecked; batch "All new" still skips it unless checked by hand. Add the key to
  `contracts/response-keys.json`.
- Footer: selection count, warning when selected rows are missing quick-add fields, primary "Add N printers".
- Batch add: concurrent `POST /printers` per row (small concurrency cap, e.g. 4); per-row success/failure; a 409
  shows "Already added" on that row.
- Model picker on every new row (pre-selected when the scan matched a model, required when it didn't — model is half
  the identity): type-ahead combobox filtering as you type, options limited to the enabled models of the plugin that
  found the IP (`GET /printers/types` filtered by the row's `plugin_id`).
- "Edit details" (current model/serial override) folds into the inline row fields.
- The full add form remains reachable ("More settings…" link per row) for anyone wanting everything up front.

## Constraints

- Vendor/provider boundary tests (`test_vendor_extraction_boundary.py`, `test_provider_boundary.py`,
  `test_no_provider_in_core.py`) must pass; quick-add/required logic only reads `ConnectionField` metadata.
- Tests/connections never block the queue loop or event loop (connect calls go through the executor as today).
- `openapi.json` regenerated (new route, changed create semantics); `contracts/response-keys.json` updated.
- Coverage floors are ratchets.

## Left to the builder

- Exact concurrency cap for batch create and for parallel auto-tests.
- Whether the in-memory unconfigured set lives in `PrinterManager` or is read from the DB per claim (must not add
  blocking I/O to the queue loop).
- Copy wording beyond what the mockup shows; chip colours follow existing tokens (`--ok`, `--warn`, `--err`).
- Test-timeout reuse (`_TEST_CONNECT_POLL_S`, 15 s) vs a shorter one for auto-tests.

## Test expectations (each must fail without its change)

- Create with only IP + model succeeds; blank fields not persisted; duplicate tuple → 409.
- Missing required field → unconfigured with reason, no client constructed, no connection attempt.
- Auto-test success → configured; failure → `config_error` set, client torn down.
- First live "online" edge flips an unconfigured printer to configured.
- PATCH with a changed connection field → unconfigured, re-tested; PATCH of name only, of a same-plugin model, or
  of identical connection values → stays configured.
- Unconfigured printer never claims (both `_try_claim_for_printer` and `is_printer_ready`), using a virtual printer.
- Migration back-fills existing printers as configured.
- Discovery: same IP + same model → `already_added`; same IP + different model → `ip_in_use_by` set, not
  `already_added`.
- Frontend: quick-add inputs rendered from `quick_add` metadata; batch add keeps results and reports per row;
  Fleet shows "Queue off" vs "Setup incomplete" distinctly.
