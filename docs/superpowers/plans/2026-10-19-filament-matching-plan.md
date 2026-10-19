# Filament matching (BIZ-312) — implementation plan

Spec: `docs/superpowers/specs/2026-10-19-filament-matching-design.md` (names and decisions recorded at its end). One PR, one commit per sub-issue.

| Sub-issue | Scope | Main files |
|---|---|---|
| BIZ-313 | Shared tuple helper; `subtype` in the inventory contract; Local plugin migration v002; Spoolman split | `services/filament_tuple.py`, `plugins/capabilities/filament_inventory.py`, `plugins/{local_inventory,spoolman}/` |
| BIZ-314 | Tuple columns on job asks / targets / project items; `filament_map` + order parts carry the tuple; migration v053 (backfill, slot subtype, filament-blocked jobs back to queued); API fields; `refs.material` and `_materials.py` removed | `models.py`, `migrations/v053_filament_tuple.py`, `api/routes/{_filament_ask,jobs,projects,orders,queue}.py` |
| BIZ-315 | Printer plugins emit the tuple; hardware replaces it and flags `reported` | `plugins/{bambu,snapmaker,elegoo_centauri}/client.py`, `printer_manager.on_ams_change`, `inventory/refs.py` |
| BIZ-316 | One matcher (`evaluate`) for single / per-colour / pinned asks; mismatch keeps the job queued; claim stores `slot_plan`; many colours on one slot | `services/filament_matching.py`, `services/queue_engine.py` |
| BIZ-317 | Job detail `filament_match` per printer; `GET /printers/{id}/slot-conflicts` | `api/routes/{jobs,printers}.py` |
| BIZ-318 | Deduction from the spool bound to each slot that printed; unbound ⇒ note + `inventory.no_spool_tracked` | `services/inventory/deduction.py`, `completion_events.py` |
| BIZ-319 | `FilamentAskEditor` + `lib/filamentAsk.ts`, tuple-based PerPrinterConfig / ProjectBuilder / orders / slot editor (+ conflict flag) / JobDetail reasons; Playwright `filament-matching.spec.ts` (mocked API or `E2E_LIVE=1` against a real backend) | `frontend/src/...`, `frontend/e2e/` |

Tests: `tests/services/test_filament_tuple.py`, `test_filament_matching.py`, `test_deduction_slots.py`, `tests/test_v053_migration.py`, `tests/api/test_filament_ask_writers.py`, engine cases in `test_queue_engine.py` / `test_queue_ordering.py`; vitest per component; Playwright as above.
