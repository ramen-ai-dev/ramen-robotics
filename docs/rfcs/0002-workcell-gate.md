# RFC 0002: `workcell_gate`, an application and workcell gate with audit dossiers

| Field | Value |
|---|---|
| Stage | 2 |
| Module | `src/ramen_robotics/workcell_gate/` |
| Status | Open for a contributor |
| Builds on | `ramen_robotics.gate` (dispatch, audit trail, `verify_audit_file`) |
| Anchors | ISO 10218-2:2025; Regulation (EU) 2023/1230 |

## Problem

Industrial robot safety is assessed for the robot application and the cell, not for the
manipulator alone. ISO 10218-2:2025 covers the integration of robot applications and robot
cells, including collaborative applications. In that vocabulary "collaborative" describes the
application or the cell: a manipulator is not "a collaborative robot" on its own. A software
gate that sits above the cell has to reason with the same unit: which application is running,
in which operating mode, with which safeguards active.

Regulation (EU) 2023/1230 on machinery applies from January 20, 2027. Integrators that put
software in the path of safety-relevant decisions need traceable records of what that software
decided, for which command, and on what evidence.

## Design

`workcell_gate` adds a cell model on top of `RoboticsGate`:

```python
@dataclass(frozen=True)
class CellProfile:
    cell_id: str
    application: str  # e.g. "machine tending, collaborative application"
    operating_mode: Literal["automatic", "manual_reduced_speed", "manual_high_speed"]
    collaborative_operation: tuple[str, ...]  # e.g. ("speed_and_separation_monitoring",)
    safeguards: tuple[str, ...]  # e.g. ("light_curtain_zone_a", "area_scanner")
    configuration_hash: str  # hash of the integrator's cell configuration


class WorkcellGate:
    def __init__(self, gate: RoboticsGate, profile: CellProfile) -> None: ...
    def dispatch(self, payload: Mapping[str, Any], *, tool_call_id: str) -> GateDecision: ...
    def export_dossier(self, directory: Path, *, since: datetime, until: datetime) -> DossierManifest: ...
```

1. **Cell-aware payloads.** `dispatch` adds the cell's state to the evaluated evidence through
   the existing payload fields (`scene_context_id` carries the cell and application identity,
   `active_hazard_flags` carries cell-level flags such as a muted safeguard). It does not add
   undeclared keys: `ManipulationDispatchPayload` forbids them. Fields the payload cannot
   express are listed as an open request to ramen foundry, not worked around.
2. **Mode rules applied locally.** Commands that conflict with the profile (for example
   automatic-mode speeds while the cell is in a manual mode) hold locally before any request.
3. **Audit dossier.** `export_dossier` writes, for a time range:
   - the audit JSONL lines (keyed receipts only; a dossier with trial receipts is refused);
   - `verify_audit_file(..., allow_trial=False)` output, which must be empty;
   - the `CellProfile`, its configuration hash, and the ramen robotics, ramen foundry and
     ramen-ai-core versions;
   - a manifest with SHA-256 digests of every file.

   Every line can be re-verified offline with the pinned public key.

## Acceptance criteria

1. No prose, identifier or default in the module calls a manipulator "collaborative"; the
   term appears only with application or cell.
2. Mode conflicts hold locally with zero requests (tested).
3. `export_dossier` refuses trial receipts and any line that fails verification, and the
   manifest digests match the files (tested).
4. A worked example in `docs/` maps each dossier field to the record it supports.

## Open questions

- The minimum cell evidence the bundle needs, and whether that requires a new payload type in
  ramen foundry.
- Retention period and storage location for dossiers.
- Clause-level mapping to ISO 10218-2:2025 and Regulation (EU) 2023/1230, and who reviews it
  before the module is marked stable.
