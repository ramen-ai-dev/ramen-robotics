# ramen robotics onboarding

A step-by-step guide for robotics engineers and the coding agents working with them (Cursor,
Claude Code, Kiro or similar). Every class, field, outcome and flag below exists in ramen
robotics 0.1.0 on ramen foundry 0.2.8 or newer. An agent can follow the sections in order.

## 0. Rules for coding agents

1. Use only the names in this guide. Do not invent tool names, payload keys or outcome values.
2. Read `RAMEN_API_KEY` from the environment only. Never print it, log it or write it to a file.
3. A robot command reaches the controller only when `RoboticsGate.dispatch()` returns
   `Outcome.RELEASED`. Every other outcome is a hold.
4. The gate runs once per resolved task or segment. It never runs inside a control loop.
5. Every live verdict carries a Schema V5 Ed25519 receipt that the gate verifies locally
   before release.
6. The keyless trial allows about 10 requests per day per network. Count your calls.

## 1. Install

```bash
git clone https://github.com/ramen-ai-dev/ramen-robotics.git
cd ramen-robotics
python3 -m venv .venv && source .venv/bin/activate     # Python 3.10 to 3.13
python -m pip install --upgrade pip
pip install -e ".[simulation]"                         # MuJoCo demos; use `-e .` without them
python -c "import ramen_robotics, ramen_foundry; print(ramen_robotics.__version__, ramen_foundry.__version__)"
```

| Extra | Adds |
|---|---|
| none | `ramen-foundry>=0.2.8,<0.3`, `ramen-ai-core>=0.3.5,<0.5` |
| `simulation` | `ramen-foundry[simulation]` (MuJoCo 3.13.0, imageio, pillow) |
| `data` | `ramen-foundry[data]` (pandas, for dataset curation) |
| `dev` | pytest, ruff, mypy |

### Environment

| Variable | Effect |
|---|---|
| `RAMEN_API_KEY` | Keyed mode against your account. Get or manage a key at https://ramenai.dev. Always wins over `--trial`. |
| `RAMEN_BASE_URL` | `https://api.ramenai.dev` (default) or `https://eu.ramenai.dev`. |
| `RAMEN_ALLOW_CUSTOM_BASE_URL` | Set to `1` to accept any other `https://` origin in `RAMEN_BASE_URL`. |
| `CI` | Any non-empty value forces the MuJoCo demo into headless mode. |

No dotenv file is read. Export variables in the shell or inject them from your secret manager.

## 2. Demos

Run from the repository root with the virtual environment active.

| Command | Live calls | Expected |
|---|---|---|
| `python demos/00_quickstart/kinematic_gate_demo.py` | 0 | three `LOCAL_BLOCK` rows, two `skipped`, exit 0 |
| `python demos/00_quickstart/kinematic_gate_demo.py --trial` | 2 | three `LOCAL_BLOCK`, thermal `BLOCKED`, `ARM-ASSY-07` `RELEASED`, one controller segment |
| `python demos/00_quickstart/asimov_robotics.py --trial` | 2 | case 2 `BLOCKED`, case 1 `RELEASED`, `Blocked-path canary executions: 0` |
| `python demos/00_quickstart/asimov_robotics.py --trial --cases 4` | 1 | `ARM-PACK-05` `BLOCKED` |
| `RAMEN_API_KEY=... python demos/00_quickstart/asimov_robotics.py` | 5 | case 1 `RELEASED`, cases 2 to 5 `BLOCKED` |
| `python demos/stage1_conflicting_goals/plunge_demo.py --trial --headless --seed 0` | 1 | `[BLOCKED]`, `host_executions=0`, `knife_clearance_m=0.150`, `ctrl_max_abs=0.000000`, `qvel_max_abs=0.000000` |
| `python demos/stage1_conflicting_goals/plunge_demo.py --ungoverned --headless` | 0 | `[DISPATCHED WITHOUT GOVERNANCE]`, `blade_contact=True` |

Every demo accepts `--audit PATH` to append one JSON line per dispatch. On macOS, the MuJoCo
viewer (omit `--headless`) needs MuJoCo's `mjpython` launcher.

When the trial allowance runs out, a demo prints the completed rows, the scope and the reset
time (00:00 UTC), skips the remaining live rows and exits 0. An unexpected outcome exits 1.

## 3. `RoboticsGate`

```python
from ramen_robotics import RoboticsGate, Outcome, verify_audit_file

gate = RoboticsGate.from_env(
    {"dispatch_manipulation": dispatch_manipulation},  # host LangChain tools
    trial=False,  # True: keyless trial when no RAMEN_API_KEY is set
    timeout_s=10.0,  # HTTP timeout per evaluation
    deadline_s=None,  # seconds from dispatch() to release; None disables
    min_interval_s=None,  # spacing between live calls; default 2.0 on the trial, 0 keyed
    audit_path="audit.jsonl",  # optional JSONL audit trail (created with mode 600)
)
decision = gate.dispatch("dispatch_manipulation", payload, tool_call_id=payload["scene_context_id"])
gate.close()  # or use `with RoboticsGate.from_env(...) as gate:`
```

Mode resolution: `RAMEN_API_KEY` set gives `keyed`; otherwise `trial=True` gives `trial`;
otherwise `local`, where payload checks run and every live dispatch returns
`HOLD_UNAVAILABLE`.

Tool registry: keys must be a subset of `dispatch_manipulation`, `adjust_plc_setpoint` and
`mutate_safety_parameter`, and each key must equal the tool's `name`. ramen foundry's
`IndustrialAutomationAgent` enforces this and evaluates every call against the
`ramen__industrial_iot_actuation_invariance` bundle with enforcement always on.

### Outcomes

| `decision.outcome` | Meaning | `executed` |
|---|---|---|
| `RELEASED` | Verified ALLOW, released before the deadline; the host tool ran once | `True` |
| `BLOCKED` | Live BLOCK; `decision.steering` has the recovery instruction | `False` |
| `LOCAL_BLOCK` | `ManipulationDispatchPayload` violation; no request sent | `False` |
| `HOLD_UNVERIFIED` | ALLOW whose receipt failed ramen foundry's or the gate's own check | `False` |
| `HOLD_TIMEOUT` | Verified ALLOW that arrived after `deadline_s`; the host tool did not run | `False` |
| `HOLD_UNAVAILABLE` | Transport or evaluation failure, or local mode | `False` |
| `HOLD_TRIAL_LIMIT` | Trial allowance used up; `decision.reason` names the reset time | `False` |
| `HOLD_TOOL_ERROR` | The host tool raised after release | `False` |

`decision.hold` is `True` for every outcome except `RELEASED`. Other fields: `reason`
(scrubbed of credentials), `tool_output`, `verdict` (`"ALLOW"`, `"BLOCK"` or `None`),
`steering`, `receipt_id`, `kid`, `receipt_class` (`"production"`, `"trial"` or `None`),
`receipt_verified` and `latency_ms`.

### How release works

The gate registers each host tool inside a release guard. When ramen foundry has a verified
ALLOW and calls the tool, the guard first re-verifies the receipt against the exact evaluated
text and checks the deadline. Only then does the host tool run. A late or unverifiable ALLOW
therefore never reaches the controller.

### Audit trail

Each dispatch appends one line with `outcome`, `executed`, `evaluated`, `verdict`,
`receipt_id`, `kid`, `receipt_class`, `steering`, `reason`, `latency_ms` and, for evaluated
calls, the exact `input_text` and the full `receipt`.

```python
verify_audit_file("audit.jsonl")  # [] when every line verifies; trial lines are reported
verify_audit_file("audit.jsonl", allow_trial=True)  # accept trial receipts
```

Trial receipts (`trial_<uuid>`) are signed with the same production key, but they come from
a shared public allowance and are not persistent audit proof. Evidence you keep uses keyed
runs.

### Scenario runs

`ramen_robotics.gate.run_scenarios(gate, scenarios)` dispatches a list of `Scenario(stage,
label, payload, expected)` in order, skips live rows in local mode and after a trial limit,
and returns a report with `exit_code`. `format_report` renders the table the demos print.

## 4. The `dispatch_manipulation` payload

`ManipulationDispatchPayload` (from ramen foundry) has exactly nine keys:

| Key | Type and rule |
|---|---|
| `robot_id` | non-blank string |
| `action_type` | one of `PICK_AND_PLACE`, `INSERT_TOOL`, `APPLY_FORCE`, `POUR_LIQUID`, `WIPE_SURFACE` (case-sensitive) |
| `target_object` | non-blank string |
| `destination_target` | non-blank string |
| `commanded_velocity_mps` | finite float, >= 0 |
| `commanded_force_nm` | finite float, >= 0 |
| `human_proximity_meters` | finite float, >= 0 |
| `active_hazard_flags` | list of strings (may be empty) |
| `scene_context_id` | non-blank string |

Missing keys, extra keys, booleans or numeric strings in number fields, NaN, infinity,
negative values and tuples instead of lists all fail locally. The error names fields and
violation kinds, never the submitted values. `ramen_foundry.manipulation_payload_problem(payload)`
runs the same check standalone.

Host tool shape:

```python
from langchain_core.tools import tool
from ramen_foundry import ManipulationActionType


@tool
def dispatch_manipulation(
    robot_id: str,
    action_type: ManipulationActionType,
    target_object: str,
    destination_target: str,
    commanded_velocity_mps: float,
    commanded_force_nm: float,
    human_proximity_meters: float,
    active_hazard_flags: list[str],
    scene_context_id: str,
) -> str:
    """Hand one released segment to the controller."""
    controller.enqueue(...)
    return "queued"
```

Wording: under ISO 10218-2:2025, "collaborative" describes the application or the cell. Name
robots neutrally (`ARM-ASSY-07`) and describe the task as running in a collaborative
application.

### PLC and safety-system tools

`adjust_plc_setpoint` keys: `node_id`, `tag`, `target_val`, `current_val`, `ramp_rate_sec`,
`engineering_units`, `asset_criticality`, `operating_envelope_id`.

`mutate_safety_parameter` keys: `node_id`, `tag`, `mutation_type`, `requested_value`,
`duration_sec`, `physical_key_interlock_verified`, `management_of_change_id`.

ramen foundry does not validate these two payloads locally; the bundle evaluates the
evidence they carry. Do not add keys.

## 5. Receipts

The gate verifies receipts with `ramen_foundry.core.receipts.signed_verdict_problem`, which
checks, from the signed bytes only:

1. the receipt has `kid`, `signature` and `canonical_payload`;
2. the Ed25519 signature verifies under the pinned key for `kid` (`ramen_pk_v1`);
3. the signed payload has `schema_version` `"5.0"`, the receipt's `kid` and `id`, and
   `payload_hash == sha256(input_text)`;
4. the signed `verdict` is `1` for ALLOW or `0` for BLOCK, matching the decision.

`input_text` is the exact evaluated string:

```python
from ramen_robotics.gate import evaluated_text

evaluated_text("dispatch_manipulation", payload)
# json.dumps({"tool": name, "arguments": payload}, sort_keys=True, separators=(",", ":"), default=str)
```

`ramen_robotics.gate.receipt_problem(receipt, input_text, allowed=True)` checks one receipt
held outside an audit file.

## 6. Placement: ROS 2, LeRobot, MuJoCo, Isaac Sim

```
operator / VLA / LLM planner
      |  resolved task: one dispatch_manipulation call
      v
RoboticsGate ──> https://api.ramenai.dev
      |  RELEASED: host tool hands the segment to the controller
      |  anything else: controller holds, planner gets steering text
      v
1 kHz controller (no network call in this loop) ──> robot or simulator
```

1. The gate sits between the planner and the controller at task or segment boundaries.
2. The host tool is the only code path to the controller.
3. On a hold, the controller keeps its last safe state or zeroes inputs and velocities, as the
   Stage 1 demo does (`data.ctrl[:] = 0`, `data.qvel[:] = 0`).
4. The planner fills the payload from perception: `human_proximity_meters` from the nearest
   human sensor, `active_hazard_flags` from scene perception, velocity and force from the
   planned segment. Reject stale perception before dispatching.
5. A multi-step task is a sequence of dispatches. Stop at the first hold.
6. Set `deadline_s` below the longest pause your task tolerates, and pre-evaluate the next
   segment while the current one runs.

ROS 2 action server (rclpy; message fields are placeholders for your action definition):

```python
def execute_callback(self, goal_handle):
    goal = goal_handle.request
    payload = {
        "robot_id": goal.robot_id,
        "action_type": goal.action_type,
        "target_object": goal.target_object,
        "destination_target": goal.destination_target,
        "commanded_velocity_mps": float(goal.max_velocity),
        "commanded_force_nm": float(goal.max_force),
        "human_proximity_meters": float(self.nearest_human_m()),
        "active_hazard_flags": list(self.hazards()),
        "scene_context_id": goal.scene_id,
    }
    decision = self.gate.dispatch("dispatch_manipulation", payload, tool_call_id=goal.scene_id)
    if decision.hold:
        self.controller.hold()
        goal_handle.abort()
        return self.make_result(False, decision.reason or decision.outcome.value)
    goal_handle.succeed()
    return self.make_result(True, "released")
```

LeRobot or another VLA loop: gate each planned chunk's skill, then stream the released chunk
at control rate without further gate calls.

```python
while running:
    observation = robot.get_observation()
    chunk = plan_chunk(policy, observation)
    decision = gate.dispatch("dispatch_manipulation", describe(chunk, observation), tool_call_id=chunk.id)
    if decision.hold:
        robot.hold()
        continue
    for action in chunk.actions:
        robot.send_action(action)
```

RFC [0001](rfcs/0001-vla-guard.md) turns this pattern into a module with a local kinematic
filter, and RFC [0003](rfcs/0003-ros2-bridge.md) into a ROS 2 goal interceptor.

## 7. Two-week integration track

### Week 1: integration proof

Integrate `RoboticsGate` into one environment (ROS 2 action server, LeRobot loop, MuJoCo or
Isaac Sim) with the host tool as the only path to the controller. Run these five payloads
with a key and `audit_path="audit.jsonl"`:

| # | Expected | Payload source |
|---|---|---|
| 1 | `BLOCKED` | `asimov_robotics.py` case 2 (thermal) |
| 2 | `BLOCKED` | case 3 (energized 480V) |
| 3 | `BLOCKED` | case 4 (speed and separation) |
| 4 | `RELEASED` | case 1 (bracket assembly) |
| 5 | `BLOCKED` | `plunge_demo.py` resolved call |

Acceptance: the outcomes match, every evaluated line has `receipt_verified: true`,
`verify_audit_file("audit.jsonl") == []`, and the controller log shows zero commands for the
blocked dispatches.

### Week 2: boundaries and edge deployment

Audit your manipulator against what the payload cannot express, and decide for each gap
whether the controller enforces it, a hazard flag represents it, or it needs a bundle
extension request:

| Boundary | In the payload | Gap to close |
|---|---|---|
| Human distance | one scalar | multiple humans, approach direction, sensor age and dropout |
| Speed | one scalar | per-link and TCP speed, speed versus separation over the segment |
| Force | one scalar | force and torque separately, contact pressure, body region |
| Trajectory | none | waypoints, keep-out volumes, joint-limit margins |
| Tool and payload | two strings | payload mass, sharp-tool state, gripper state |
| Stopping | none | stopping time and distance, brake health |
| Machine state | none | protective stop state, operating mode |
| Hazard source | flag list | flag vocabulary, producer of each flag, stale perception |
| Evidence provenance | host-supplied | attestation of sensor-derived fields |

Measure the gate: run at least 50 live calls and report median, p95 and maximum
`latency_ms`. Use the numbers to choose segment length and `deadline_s`.

## 8. Verification checklist

```yaml
checks:
  - cmd: python -m pytest -q
    expect: all tests pass, no network access
  - cmd: python demos/00_quickstart/kinematic_gate_demo.py
    expect: exit 0, three LOCAL_BLOCK rows
  - cmd: python demos/00_quickstart/kinematic_gate_demo.py --trial
    expect: exit 0, BLOCKED and RELEASED rows, receipts "ramen_pk_v1 verified (trial)"
  - cmd: python demos/stage1_conflicting_goals/plunge_demo.py --trial --headless --seed 0
    expect: "[BLOCKED] mode=governed", host_executions=0, knife_clearance_m=0.150
  - cmd: python demos/00_quickstart/asimov_robotics.py
    expect: with RAMEN_API_KEY, 1 RELEASED, 4 BLOCKED, "Blocked-path canary executions: 0"
```

Further reading: https://ramenai.dev/llms.txt and https://github.com/ramen-ai-dev/ramen-foundry.
