<p align="center">
  <img src="docs/brand/ramen-logo.png" alt="ramen ai" width="96">
</p>

<h1 align="center">ramen robotics</h1>

<p align="center">
  A signed task-dispatch gate between robot planners and controllers, built on
  <a href="https://github.com/ramen-ai-dev/ramen-foundry">ramen foundry</a>.
</p>

ramen robotics puts one governed entry point between a planner (a VLA, an LLM or a task
scheduler) and the controller that moves a robot. Every resolved manipulation command is
checked locally against a strict payload contract, evaluated live by the ramen ai
`ramen__industrial_iot_actuation_invariance` bundle, and released to the controller only
when a Schema V5 Ed25519 receipt for that exact command verifies on your machine. Every
other outcome is a deterministic hold.

```
planner (VLA / LLM / scheduler)
      |  one resolved dispatch_manipulation call
      v
RoboticsGate ── local contract check ── live verdict (https://api.ramenai.dev)
      |          local receipt check     Schema V5 Ed25519 receipt
      |          deadline check          JSONL audit line
      v
RELEASED: host tool hands the segment to the controller
anything else: the host tool never runs and the controller holds
```

## Install

Python 3.10 to 3.13.

```bash
git clone https://github.com/ramen-ai-dev/ramen-robotics.git
cd ramen-robotics
python -m venv .venv && source .venv/bin/activate
pip install -e ".[simulation]"        # or `pip install -e .` without MuJoCo
```

This installs [`ramen-foundry`](https://pypi.org/project/ramen-foundry/) 0.2.8 or newer from
PyPI. ramen robotics builds on its published API and contains no copy of it.

## Quickstart

No API key is needed. `--trial` uses the keyless public trial, which allows about 10
evaluations per day per network.

```bash
python demos/00_quickstart/kinematic_gate_demo.py --trial
python demos/00_quickstart/asimov_robotics.py --trial
python demos/stage1_conflicting_goals/plunge_demo.py --trial --headless
python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial
```

With a key from [ramenai.dev](https://ramenai.dev), export `RAMEN_API_KEY` and drop
`--trial`. A key in the environment always takes precedence.

| Demo | What it shows | Live calls |
|---|---|---|
| `00_quickstart/kinematic_gate_demo.py` | Three malformed payloads blocked locally, an aerosol canister onto an active burner blocked live, a de-rated bracket transfer released live | 2 |
| `00_quickstart/asimov_robotics.py` | ASIMOV and RoboHarm cases: thermal, electrical, speed and separation, chemical | 2 on `--trial`, 5 with a key |
| `stage1_conflicting_goals/plunge_demo.py` | "Cut the bread" resolves to a knife plunge toward a human-like target in a MuJoCo scene; the gate blocks it and the arm holds 15 cm above the target | 1 |
| `stage2_industrial_packaging/packaging_cell_demo.py` | A food-packaging cell under a rush order: six requests held by local cell checks (tampered order, unverified allergen cleaning, wrong gripper, fogged camera, freezer setpoint, recipe export), a light-curtain mute and a close-approach move blocked live, a compliant batch released, and a tamper-evident dossier | 3 |

## Use the gate

```python
from langchain_core.tools import tool
from ramen_robotics import RoboticsGate, verify_audit_file


@tool
def dispatch_manipulation(
    robot_id: str,
    action_type: str,
    target_object: str,
    destination_target: str,
    commanded_velocity_mps: float,
    commanded_force_nm: float,
    human_proximity_meters: float,
    active_hazard_flags: list[str],
    scene_context_id: str,
) -> str:
    """Hand one released segment to the controller."""
    controller.enqueue(...)  # the only path to the controller
    return "queued"


with RoboticsGate.from_env(
    {"dispatch_manipulation": dispatch_manipulation}, trial=True, deadline_s=2.0, audit_path="audit.jsonl"
) as gate:
    decision = gate.dispatch("dispatch_manipulation", payload, tool_call_id=payload["scene_context_id"])
    if decision.hold:
        controller.hold()

problems = verify_audit_file("audit.jsonl")  # trial receipts are reported here
```

| `decision.outcome` | Meaning | Host tool ran |
|---|---|---|
| `RELEASED` | Verified ALLOW, released before the deadline | yes, once |
| `BLOCKED` | Live BLOCK; `decision.steering` explains it | no |
| `LOCAL_BLOCK` | Payload broke the `ManipulationDispatchPayload` contract; nothing was sent | no |
| `HOLD_UNVERIFIED` | ALLOW whose receipt did not verify locally | no |
| `HOLD_TIMEOUT` | Verified ALLOW arrived after `deadline_s` | no |
| `HOLD_UNAVAILABLE` | Transport or evaluation failure, or local mode | no |
| `HOLD_TRIAL_LIMIT` | Trial allowance used up; `decision.reason` gives the reset time | no |
| `HOLD_TOOL_ERROR` | The host tool raised after release | started, then raised |

Configuration comes from the process environment only:

| Variable | Effect |
|---|---|
| `RAMEN_API_KEY` | Keyed mode. Never logged; scrubbed from every reason and log line. |
| `RAMEN_BASE_URL` | `https://api.ramenai.dev` (default) or `https://eu.ramenai.dev`. |
| `RAMEN_ALLOW_CUSTOM_BASE_URL=1` | Allow any other `https://` origin in `RAMEN_BASE_URL`. |

Trial receipts (`trial_<uuid>`) verify under the same pinned key, but they come from a
shared public allowance. `verify_audit_file` reports them unless you pass
`allow_trial=True`, so an audit file built for compliance evidence needs a keyed run.

## Repository layout

| Path | Contents |
|---|---|
| `src/ramen_robotics/gate/` | `RoboticsGate`, receipt checks, release guard, audit trail, scrubbing |
| `src/ramen_robotics/workcell_gate/` | `WorkcellGate`: cell state contract, workcell pre-checks, hash-chained journal, dossier exporter |
| `demos/` | Runnable demos; each one builds on a module under `src/ramen_robotics/` |
| `assets/scenes/` | Text-only MuJoCo scenes built from primitive geoms |
| `benchmarks/` | Contributor benchmarks |
| `docs/ONBOARDING.md` | Step-by-step guide for robotics engineers and coding agents |
| `docs/rfcs/` | Designs for contributor-owned modules |

## Roadmap

The modules below are open for contributors. Each one has a design RFC and is built as a
new package under `src/ramen_robotics/`.

| Stage | Module | RFC |
|---|---|---|
| 1 | `vla_guard`: System 2 VLM task and skill gate plus a System 1 local kinematic envelope filter over 30 to 200 Hz action chunks | [0001](docs/rfcs/0001-vla-guard.md) |
| 2 | `workcell_gate` (v1 implemented): application and workcell checks anchored to ISO 10218-2:2025, tamper-evident dossiers for Regulation (EU) 2023/1230 traceability records; PLC, MES and OPC UA state adapters open | [0002](docs/rfcs/0002-workcell-gate.md) |
| 3 | `ros2_bridge`: ROS 2 action goal interceptor and BehaviorTree precondition ahead of MoveIt 2 and Nav2 | [0003](docs/rfcs/0003-ros2-bridge.md) |
| 4 | `mission_and_data`: drone and AMR mission gating, trajectory dataset curation | [0004](docs/rfcs/0004-mission-and-data.md) |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Tests run offline with a socket guard; CI runs
them on Python 3.10 to 3.13 together with ruff, mypy, a secret-pattern check and a file-size
guard.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
