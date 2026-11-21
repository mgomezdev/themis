# Validate printers after a develop build

Use this checklist for the owned Bambu P1S, Elegoo Centauri Carbon and Snapmaker U1. Their manufacturer plugins serve those printers; generic Klipper is a separate, manufacturer-neutral option. Keep each physical printer in exactly one plugin binding.

Record the build SHA shown in Settings → About, printer model/firmware, plugin, and each result below. The existing U1 scan/connection is owner-confirmed. The new topology reads, refresh behavior and print routing still need physical-printer verification; automated virtual-printer and mock-app tests are recorded in the implementation plan.

## Refresh the existing printer

1. Start with the printer idle and the queue paused. Open its Fleet edit dialog and select **Refresh topology**. Keep the existing printer record and connection.
2. Confirm each tool's installed nozzle size. If hardware cannot report sizes, enter **Installed nozzle sizes (mm)**, one per tool in the displayed order, then refresh. Elegoo Centauri Carbon requires its installed size to be supplied explicitly. Mixed sizes must remain distinct on multi-tool printers.
3. Confirm configured filament positions and loaded contents. Empty positions count toward capacity; they cannot satisfy a filament request. Duplicate material in separate positions must remain separate choices.
4. Change a position's contents, including loaded → empty → loaded, and refresh. Confirm the printer ID/plugin binding and connection remain unchanged and the new contents appear in Fleet.
5. After changing a nozzle or supported feeder configuration, refresh again. A P1S can have no AMS or multiple AMS units; counts must reflect this instance rather than a fixed model-wide capacity. No remove/re-add or reconnect action should be needed. Snapmaker U1 must retain all four positions, including empty ones.
6. Refresh an unchanged printer repeatedly. `GET /api/v1/printers/{id}` should show the same `topology_revision`. A real topology/material change advances it; an incomplete/failed refresh retains the prior snapshot and reports `topology_error` until a successful refresh.

For a read-only U1 protocol check from `backend/`, with the printer URL supplied locally:

```sh
THEMIS_VERIFY_MOONRAKER_URL=http://PRINTER_IP:7125 \
  pytest protocol_verification/test_moonraker_status.py -k live_topology -m real_protocol -v -s
```

Set `THEMIS_VERIFY_MOONRAKER_API_KEY` locally if required. See the [protocol verification guide](../backend/protocol_verification/README.md) for the other integrations and exact environment variables. These protocol checks do not start prints.

## Verify scheduling and printing

- Use a small known print for each owned printer. Confirm the selected material/position and nozzle size are reflected in slicing and the actual print. On the U1, include a request that selects a later tool and one using different nozzle sizes where configured.
- On a printer with multiple loaded choices, enable the single-filament policy. Any one loaded filament must remain selectable; a request needing multiple filaments must be ineligible. Disable the policy and verify supported swapping with a small multi-filament print.
- Prepare work, then change its material/topology before dispatch. The old preparation must be blocked for re-evaluation; it must not start with an obsolete assignment. A refresh during an upload must also prevent a stale start. Stored-file and direct upload-and-print actions require valid topology too.
- Select a saved slice, change installed nozzle sizes while retaining the same machine profile, and refresh. The old saved slice must not be offered as printable or start. Legacy saved slices without recorded sizes require reslicing. Export/restore the fleet and confirm manual nozzle configuration survives while topology is freshly acquired.
- Confirm completion, history and the plate-clear gate still behave normally. Refreshing an already-running printer must not rewrite that print's assignment.
- Check that generic Klipper advertises no manufacturer-specific models. Trying to add the same configured host/serial through another plugin must report an ownership conflict, preserving the original binding.

Record failures with the build SHA, printer/firmware, operation, expected versus observed topology, and relevant redacted logs. Keep remaining physical-protocol findings under the corresponding manufacturer issue rather than treating the mock checks as hardware evidence.

See [the architecture and call path](printer-interface.md#runtime-use-and-dispatch-safety) for the refresh/dispatch boundary.
