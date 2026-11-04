# BIZ-359 True per-plate part counts Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** `jobs.project_item_quantities` holds the real per-plate counts from the pack's manifest instead of the round-robin guess, so outcome accounting and project progress match what printed.

**Architecture:** The neutral `ProjectConstructor` contract gains an optional `plate_manifest(project_3mf: Path) -> PlateManifest | None` (default None: the provider reports none). Laminus implements it by reading `Metadata/laminus_manifest.json` (laminus#43, feature `pack.manifest`). Core maps manifest input indexes → library files → project items (fill items of one file in order) in a pure function, and falls back to round-robin (logged) when there's no manifest or it doesn't add up. Reused packs read the manifest from the stored file.

**Spec:** design record § Queue ("The slicer returns a manifest of parts per plate. Per-part counts replace the round-robin guess everywhere"); Linear BIZ-359; Laminus `docs/laminus-api-for-agents.md` § Plate manifest.

## Global Constraints
- Core never parses provider-specific files: the manifest file name / schema lives only in `providers/laminus/`.
- Storage shape unchanged: `{str(project_item_id): count}` per job (STL-only today ⇒ part = item; bundles come with BIZ-356).
- A manifest whose per-input totals ≠ requested quantities is ignored (fallback + warning), never trusted partially.

## Review Focus
1. Two project items sharing one STL file (summed into one pack input) → counts split by item order, totals exact. → Task 2 test.
2. Reused pack (allow_cached) → manifest read from the stored file, same counts as a fresh pack. → Task 3 test.
3. Old pack / provider without manifest → round-robin exactly as before. → Task 2 test.
4. Plate in manifest that `_parse_plate_nums` doesn't list (or vice versa) → fallback. → Task 2 test.
5. An item with qty 0 on a plate is omitted or 0, consistently with the round-robin output today (keep 0 entries so outcome code sees every item). → Task 2 test.

### Task 1: Contract + Laminus reader
Files: `backend/app/services/providers/contract.py` (`PlateManifest` dataclass: `plates: dict[int, dict[int, int]]` plate → {input index → instances}; `ProjectConstructor.plate_manifest` default None), `backend/app/services/providers/laminus/constructor.py` (read the JSON from the zip; bad/missing/unknown version → None), tests in `backend/tests/providers/` (+ conformance kit if ProjectConstructor has one).

### Task 2: Pure mapping
`projects._plate_item_quantities(manifest, wanted, group_items, plate_nums) -> list[dict[str, int]] | None` (None = fallback). Tests per Review Focus 1, 3, 4, 5.

### Task 3: Wire into generate
Replace the round-robin block in `_generate_project` (fresh + reused pack); manifest read via `asyncio.to_thread(store.materialize …)`. API test with a fake constructor whose manifest gives plates different mixes → each job's `project_item_quantities` exact; `PUT /jobs/{id}/outcome` progress matches.

### Task 4: Docs + checks + review + PR
`docs/provider-interfaces.md` (new optional method), `docs/agent/data-model.md` (project_item_quantities source), full suites, reviewer, PR.
