# RFC 0004: `mission_and_data`, mission gating and trajectory dataset curation

| Field | Value |
|---|---|
| Stage | 4 |
| Module | `src/ramen_robotics/mission_and_data/` |
| Status | Open for a contributor |
| Builds on | `ramen_robotics.gate`, `ramen_foundry.data` (`pip install -e ".[data]"`) |

This RFC covers two physical-AI extensions that reuse the gate and the receipt trail.

## Part A: drone and AMR mission gating

Mobile robots receive missions, not manipulation segments: a route, a geofence, a payload, a
time window. Each mission, and each re-plan, is a dispatch boundary where the same rules
apply: evaluate the resolved mission before it is uploaded to the vehicle, release only on a
verified ALLOW, hold otherwise.

```python
@dataclass(frozen=True)
class MissionPlan:
    vehicle_id: str
    kind: Literal["aerial", "ground"]
    waypoints: tuple[Waypoint, ...]
    geofence_id: str
    payload_description: str
    max_speed_mps: float
    min_human_separation_m: float
    active_hazard_flags: tuple[str, ...]


class MissionGate:
    def __init__(self, gate: RoboticsGate, uploader: BaseTool) -> None: ...
    def dispatch(self, plan: MissionPlan) -> GateDecision: ...
```

Dependency: `IndustrialAutomationAgent` registers only `dispatch_manipulation`,
`adjust_plc_setpoint` and `mutate_safety_parameter`. A mission tool needs a matching template
and bundle in ramen foundry. Until that exists, Part A delivers the plan model, the local
checks (geofence containment, finite values, speed bounds, stale telemetry) and the uploader
wrapper, with the live path behind an explicit feature flag. It does not send missions
through `dispatch_manipulation`.

## Part B: robotics trajectory dataset curation

Teleoperation and policy-rollout datasets carry episodes whose task text or annotations
describe unsafe behaviour. Training on them teaches the hazard. `ramen_foundry.data`
evaluates tabular data row by row (`filter_dataframe`, `filter_csv`, strict exclusion or
semantic repair through a callback) and works with a key or with the keyless trial.

`mission_and_data` adds a thin adapter:

```python
def curate_episodes(
    episodes: pd.DataFrame,  # one row per episode: task text, annotations, scene id
    *,
    bundle_id: str = "ramen__industrial_iot_actuation_invariance",
    mode: Literal["strict"] = "strict",
    client: RamenClient | TrialComplianceClient | None = None,
) -> CurationReport: ...
```

The report lists kept and excluded episode ids with the receipt id of each verdict, so a
curated dataset can be traced back to signed evidence. Trajectory arrays are never sent; only
the episode text columns are evaluated.

## Acceptance criteria

1. Part A: geofence, speed and staleness checks hold locally with zero requests (tested); the
   live path stays disabled until a mission template exists upstream.
2. Part B: `curate_episodes` calls `ramen_foundry.data.filter_dataframe` and adds no
   evaluation logic of its own; tests use the fake server.
3. Example: curating a small public episode index, with its license stated.

## Open questions

- The mission payload contract to propose to ramen foundry.
- Which dataset formats to support first (for example LeRobot episode metadata).
- Batch sizes and pacing for curation runs on the trial allowance.
