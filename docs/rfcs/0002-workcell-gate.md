# RFC 0002: `workcell_gate`, an application and workcell gate with audit dossiers

| Field | Value |
|---|---|
| Stage | 2 |
| Module | `src/ramen_robotics/workcell_gate/` |
| Status | Implemented (v1 reference module). Real PLC, MES and OPC UA `StateProvider` adapters are open for contributors |
| Builds on | `ramen_robotics.gate` (dispatch, audit trail, `verify_audit_file`), `ramen_foundry.Ed25519TokenVerifier` |
| Anchors | ISO 10218-2:2025; Regulation (EU) 2023/1230 |
| Reference demo | `demos/stage2_industrial_packaging/packaging_cell_demo.py` |

## Problem

Industrial robot safety is assessed for the robot application and the cell, not for the
manipulator alone. ISO 10218-2:2025 covers the integration of robot applications and robot
cells, including collaborative applications. In that vocabulary "collaborative" describes the
application or the cell: a manipulator is not "a collaborative robot" on its own. A software
gate that sits above the cell has to reason with the same unit: which application is running,
in which operating mode, with which safeguards active.

Regulation (EU) 2023/1230 on machinery applies from January 20, 2027. Integrators that put
software in the path of safety-relevant decisions need traceable records of what that software
decided, for which command, on what evidence, and that those records were not altered.

## Scope

v1 works at the supervisory dispatch layer, roughly 0.01 to 5 Hz: batch starts, motion
segments, PLC setpoint changes, safety-parameter changes and recipe exports. It reads the health
that perception systems report about themselves and does not process images, run in a servo
loop, or replace any safety function; the cell's safety-rated devices and safety PLC stay
authoritative.

## Design (v1)

```
StateProvider (MES / PLC / OPC UA / SCADA adapter) ──> CellSnapshot
                                                         |
WorkcellRequest ──> pre-checks (deterministic, < 1 ms) ──┤ any finding: LOCAL_BLOCK, no request
                                                         v
                    to_dispatch() ──> RoboticsGate ──> ramen__industrial_iot_actuation_invariance
                                                         | RELEASED only on a verified ALLOW
                    WorkcellJournal (hash chain)  <──────┘ every decision, hold or release
                    export_dossier() / verify_dossier()
```

### Cell model

- `CellProfile` (static): application, robots, safeguards, collaborative operation, per-mode
  `SpeedEnvelope` (speed ceiling, separation floor), per-tag `PlcEnvelope` (inclusive bounds,
  slew limit), declared safety tags, allergen matrix, permitted tooling per recipe, `EgressPolicy`,
  and pinned Ed25519 issuers for work orders, sanitation clearances and key evidence.
  `config_hash()` is recorded with every decision.
- `CellSnapshot` (runtime): operating mode, mounted gripper, loaded and previous recipe,
  sanitation token, safeguard states, lockout, nearest-person distance, PLC values, sensor health.
  Operating mode lives here, not in the profile, because it changes at runtime.
- `StateProvider.snapshot()` is the adapter interface. v1 ships `StaticStateProvider` for tests and
  simulators.

### Requests

`BatchStart`, `CellMotion` (both `dispatch_manipulation`), `SetpointChange`
(`adjust_plc_setpoint`), `SafetyParameterChange` (`mutate_safety_parameter`) and `RecipeExport`
(local only). Cell context travels in declared payload fields: `scene_context_id` is
`<cell>/<mode>/cfg-<12 hex>/<ref>` and `active_hazard_flags` carries hazards derived from the
snapshot. No undeclared keys are added; `ManipulationDispatchPayload` forbids them. `rush` is
recorded and never relaxes a check or enters the payload.

### Pre-checks

| Check | Holds on |
|---|---|
| `work_order` | Order token unsigned, from an unpinned issuer, expired, or not binding cell, order, recipe, lot and quantity |
| `changeover` | Recipe not declared or not loaded; mounted gripper not permitted; allergen carryover without a pinned, matching sanitation token with `swab_result == "PASS"` |
| `sensor_health` | Required sensor missing, reported fault (for example `OPTICS_FOGGED`), confidence below the floor, stale reading, no separation reading |
| `safety_state` | Active lockout; safeguard not healthy |
| `motion_envelope` | Unknown robot; batch start outside automatic mode; speed above the mode ceiling; separation below the mode floor |
| `plc_envelope` | Undeclared tag; no current value; target outside inclusive bounds; non-positive ramp; slew above the limit |
| `safety_parameter` | Undeclared safety tag; invalid duration or change id; `physical_key_interlock_verified` asserted without a matching lockout token |
| `egress` | Destination not allowlisted; classified fields to a non-classified destination; undeclared recipe |

Every applicable check runs and every finding is recorded. A check that raises is a hold.

### Routing

v1 evaluates every live request through `RoboticsGate` on
`ramen__industrial_iot_actuation_invariance`. `RecipeExport` has no industrial tool: a denied
export is `LOCAL_BLOCK`, and an export that clears the local checks is reported as
`stage="local_only"` with `HOLD_UNAVAILABLE`, never `RELEASED`. Multi-bundle routing (for example
`ramen__shield_core_it` for data egress) needs a generalization of `RoboticsGate` and is left to a
separate core change.

### Journal and dossier

`WorkcellJournal` writes one JSON line per decision with `prev_sha256` and `entry_sha256`.
`export_dossier(journal, receipts, out_dir, profile=..., since=..., until=..., allow_trial=False)`
joins journal entries to the gate's receipt lines by `tool_call_id` and writes `manifest.json`,
`interventions.jsonl`, `receipts.jsonl`, `verification.json` and `DOSSIER.md`. Export refuses a
broken chain, a missing or inconsistent join, a receipt that does not verify, a different cell
configuration, an existing output directory, or a trial receipt unless `allow_trial=True` (the
dossier is then labeled `evidence_class: trial`). `verify_dossier(out_dir)` repeats every check
offline. v1 does not sign the manifest; file digests, the hash chain and the Ed25519 receipts carry
the integrity.

## Acceptance criteria

1. No prose, identifier or default in the module calls a manipulator "collaborative"; the
   term appears only with application or cell. *(Met: tests check the demo profile and rows.)*
2. Mode and envelope conflicts hold locally with zero requests. *(Met.)*
3. `export_dossier` refuses trial receipts by default and any line that fails verification, and
   the manifest digests match the files. *(Met, including tamper tests.)*
4. A worked example maps each dossier field to the record it supports. *(Met:
   `demos/stage2_industrial_packaging/README.md`.)*

## Open for contributors

- `StateProvider` adapters for real PLCs, MES systems and OPC UA servers, each as an optional
  extra with its license stated.
- Additional pre-checks (for example metal-detector or checkweigher reject status).
- Multi-bundle routing once `RoboticsGate` is generalized.

## Open questions

- The minimum cell evidence the bundle needs, and whether that requires a new payload type in
  ramen foundry.
- Retention period and storage location for dossiers, and optional host-held manifest signing.
- Clause-level mapping to ISO 10218-2:2025 and Regulation (EU) 2023/1230, and who reviews it
  before the mapping is published.
