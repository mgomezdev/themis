# Live printer topology implementation

Scope: the current Bambu, Elegoo, Snapmaker and generic Klipper integrations.
No new manufacturer integration or framework dependency. Keep physical printer
identity and sessions stable. Use the existing single-filament policy.

Test-first evidence: schema/refresh API initially failed because the contract and
route were absent. Registration tests failed for missing refresh/artifact
support. Protocol tests failed on missing refresh implementations; boundary tests
failed on core AMS mapping, G-code recipes and manufacturer aliases. Dispatch
tests demonstrated starts after cancellation, removed configuration, stale
revision and topology changes during upload. Direct upload and stored-file start tests also demonstrated a missing/pending topology bypass before those paths were guarded. Per-tool sizes were absent from
slicing/cache identity before their tests. The UI notification test failed until
topology frames triggered refetch. Fixes retain explicit failure behavior rather
than defaulting missing hardware data.

Implementation: neutral validated snapshots; v082 persistence; one locked live
refresh/publication path for user requests and plugin notifications; stable empty
positions; per-tool nozzle sizes in slicing/cache inputs; stale assignment guards
before upload/start; plugin-owned protocol, mapping and compatibility data; UI
refresh and manual nozzle input. Existing queue fixtures explicitly declare their
simulated hardware instead of relying on production hardware defaults.

Validation: targeted tests during each red/green step, full backend/frontend
suites, backend coverage floor, lint/build and scratch-instance API/UI checks.
Results: backend coverage run passed 4,317 tests (3 skipped), 92.43% coverage against the 92% floor.
Frontend passed 1,631 tests. Final regression: 4,320 backend tests passed, 3 skipped, including direct-start guards. Focused final critical-path checks passed 135 tests, including repeated no-op refreshes.
Scratch production-app startup applied v081/v082 and public API calls verified position expansion, empty-to-loaded
changes, nozzle updates, failure retention/recovery, policy preservation and the same client/plugin binding.
A real browser against that scratch app refreshed the displayed nozzle from 0.6 to 0.8 mm with no page errors.
No commands were sent to physical printers; the owner's earlier U1 connection confirmation remains the hardware evidence.
