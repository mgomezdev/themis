# Real-protocol verification (manual)

Themis talks to printers over vendor protocols we implement **from the documentation** (Bambu FTPS, Moonraker,
Elegoo SDCP). The normal test suite (`backend/tests`) proves our code against *virtual printers*
(`tests/virtual_printers/`) that encode what the docs say. This suite checks the other half: **does a real printer
actually behave the way the docs — and therefore our virtual printers — say it does?**

It is **not** part of the quality gates: it is outside `testpaths`, CI never runs it, and every test skips unless
you point it at a printer.

```bash
cd backend
# Bambu (LAN mode, access code from the printer's screen)
THEMIS_VERIFY_BAMBU_HOST=192.168.7.20 THEMIS_VERIFY_BAMBU_ACCESS_CODE=12345678 \
    pytest protocol_verification -m real_protocol -v -s

# Moonraker / Snapmaker U1 (host:port, optional API key)
THEMIS_VERIFY_MOONRAKER_URL=http://192.168.7.30:7125 [THEMIS_VERIFY_MOONRAKER_API_KEY=...] \
    pytest protocol_verification -m real_protocol -v -s

# Discovery: a range containing the printers above (also needs the per-vendor host vars)
THEMIS_VERIFY_DISCOVERY_RANGE=192.168.7.0/24 THEMIS_VERIFY_BAMBU_HOST=... pytest protocol_verification/test_discovery.py -v -s

# Spoolman absolute weight (the TEST spool's weight is overwritten, then restored; needs ALLOW_WRITE for the writes)
THEMIS_VERIFY_SPOOLMAN_URL=http://spoolman:7912 THEMIS_VERIFY_SPOOLMAN_SPOOL_ID=7 [THEMIS_VERIFY_SPOOLMAN_API_KEY=...] \
    [THEMIS_VERIFY_ALLOW_WRITE=1] pytest protocol_verification/test_spoolman_weight.py -m real_protocol -v -s

# Elegoo Centauri (SDCP websocket, port 3030)
THEMIS_VERIFY_ELEGOO_HOST=192.168.7.40 pytest protocol_verification -m real_protocol -v -s
```

Use `-s` to see what each test observed (raw LIST lines, raw SDCP responses) — when a test fails, that output is the
evidence for updating the client **and** the matching virtual printer so the two stay in agreement.

## Safety

* Default is **read-only**: connect, list, read metadata, download a small existing file.
* Anything that writes (upload a sacrificial `themis-verify-<timestamp>.gcode`, then delete it) needs
  `THEMIS_VERIFY_ALLOW_WRITE=1`. These tests only ever delete the file they just created.
* Nothing here starts a print, moves an axis or changes a temperature.
* Run against an idle printer.

## Adding a protocol

When a feature relies on a vendor protocol we can't exercise in CI:

1. Implement it against the **documented** protocol, citing the doc in the code.
2. Add/extend a virtual printer in `backend/tests/virtual_printers/` and gate/action tests that use it.
3. Add a `test_<vendor>_<feature>.py` here that asserts, against a real device, each assumption the virtual
   printer encodes (response shapes, units, ordering, error behaviour). Read-only first; writes opt-in.

## Known gaps (need a printer *and* a sacrificial print — not automated)

* Bambu HMS severity nibble (`code >> 16`: 1 fatal … 4 info), the module table, the wiki URL pattern (`https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/AAAA_BBBB_CCCC_DDDD` — open one to confirm), and the SDCP `ErrorNumber` table (1–5): only provable by provoking real faults.
* Bambu `start_print` payload for a stored file (`param`, `url` for root vs `/cache` files).
* Elegoo delete / start on a listed id, and how SDCP marks directories (`type`).
* Whether Bambu reports `subtask_name` with or without the extension while printing (the delete guard compares stems).

## What is verified today

| Suite | Assumptions checked |
|---|---|
| `test_bambu_files.py` | implicit FTPS on :990 with `bblp` + access code and `PROT P`; `LIST` lines parse as `ls -l`; listed sizes equal `SIZE`; `/cache` presence; download = listed size and is a zip; (write) STOR → listed → DELE round trip |
| `test_moonraker_files.py` | `/server/info` reachable; `/server/files/directory?extended=true` shape (`dirs[].dirname`, `files[].filename/size/modified`, metadata keys & units); client listing agrees with the raw API; download = listed size; (write) upload → listed → delete |
| `test_moonraker_status.py` | `display_status.progress` is a 0..1 fraction; `print_stats`/`heater_bed`/`extruder`/`toolhead`/`webhooks` carry the keys the client reads; `camera_mjpeg_url` answers `multipart/x-mixed-replace` (the U1's `/webcam/stream` is a 404, `/webcam/stream.mjpg` is the stream) |
| `test_discovery.py` | Bambu: MQTT 8883 + FTPS 990 open; unicast SSDP `M-SEARCH` on UDP 1990 answers with `USN`/`DevModel.bambu.com`/`DevName.bambu.com`; multicast NOTIFY parses. Elegoo: unicast `M99999` on UDP 3000 → SDCP JSON (`MainboardID`, `MachineName`, `Name`, `MainboardIP`). Moonraker: `/server/info` signature (200, or 401/403 when a key is required). Whole sweep of `THEMIS_VERIFY_DISCOVERY_RANGE` finds every configured printer |
| `test_alarms.py` | Bambu `print.hms` = list of `{attr:int, code:int}` and carries the full current list; entries decode to known modules + severities (set `THEMIS_VERIFY_BAMBU_SERIAL` too). Elegoo `Status.PrintInfo.ErrorNumber` is an int. Moonraker `webhooks.state/state_message` and `print_stats.state/message` exist and the client's alarms match them. Observational on a healthy printer — trigger a harmless fault to exercise decoding |
| `test_elegoo_files.py` | SDCP `GET_FILE_LIST` response shape for `/local/` (entry keys, how directories are marked, name format); client listing agrees; ids round-trip into `start_print`'s `/local/` prefix rule |
| `test_spoolman_weight.py` | `GET /spool/{id}` carries `remaining_weight`/`used_weight` and remaining = initial − used; (write; target derived from the spool, original `used_weight` restored and verified — a failed restore fails loudly with the value to put back) `PATCH {remaining_weight: N}` reads back exactly N with `used_weight = initial − N` (not recomputed later); sending it twice is a no-op; sending both weights is a 400. Backs the deduction model's absolute set (BIZ-202 D6) |
