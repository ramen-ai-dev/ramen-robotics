# RFC 0001: `vla_guard`, a dual-system guard for VLA action chunks

| Field | Value |
|---|---|
| Stage | 1 |
| Module | `src/ramen_robotics/vla_guard/` |
| Status | Open for a contributor |
| Builds on | `ramen_robotics.gate.RoboticsGate` |
| Reference demo | `demos/stage1_conflicting_goals/plunge_demo.py` |

## Problem

Vision-language-action (VLA) policies turn an instruction and an observation into short
chunks of low-level actions, typically 30 to 200 Hz. A mundane instruction ("cut the bread",
"tidy the counter", "hand me that") can resolve to a physically hazardous motion: the knife
goes toward the human-like object, the kettle goes onto the cable. The hazard sits in the
resolved target and motion, not in the words, so it has to be checked after the policy has
resolved what it will do and before the controller moves.

One live evaluation is a network round trip, so it cannot run per action at control rate.
The guard therefore splits the check in two.

## Design

```
instruction + observation
      |
  VLA policy ──> skill proposal (target, tool, destination, speed and force bounds)
      |
  System 2: semantic task and skill gate       one RoboticsGate.dispatch per skill
      |   RELEASED  ──> SkillEnvelope (bounds signed by the released receipt id)
      |   otherwise ──> hold, steering text back to the planner
      v
  action chunks at 30-200 Hz
      |
  System 1: local kinematic envelope filter    < 1 ms per chunk, no network
      |   inside envelope ──> controller
      |   outside         ──> clamp or hold, report to System 2
      v
  controller (1 kHz) ──> robot
```

**System 2, the semantic gate.** At each skill or subtask boundary, the guard converts the
VLA's skill proposal into one `dispatch_manipulation` payload (`target_object`,
`destination_target`, `commanded_velocity_mps`, `commanded_force_nm`,
`human_proximity_meters`, `active_hazard_flags`, `scene_context_id`) and dispatches it
through `RoboticsGate`. Only `Outcome.RELEASED` produces a `SkillEnvelope`. The registered
host tool is the function that installs the envelope, so an envelope cannot exist without a
verified ALLOW.

**System 1, the kinematic envelope filter.** Every action chunk is checked locally against
the active envelope before it reaches the controller:

- every value finite;
- joint positions inside limits, with a margin;
- TCP speed and commanded force at or below the released bounds;
- the TCP outside keep-out volumes (for example a sphere around each tracked human);
- the envelope not expired and perception not stale.

A chunk that fails is clamped (when the envelope allows clamping for that bound) or replaced
by a hold. The filter allocates nothing per call and makes no network call. Its budget is
under 1 ms per chunk for a 7-DoF arm and 50-step chunks on a laptop CPU.

## Proposed interface

```python
@dataclass(frozen=True)
class SkillEnvelope:
    receipt_id: str
    max_tcp_speed_mps: float
    max_force_n: float
    joint_limits: tuple[tuple[float, float], ...]
    keep_out: tuple[KeepOutSphere, ...]
    expires_at: float  # monotonic seconds


class VlaGuard:
    def __init__(self, gate: RoboticsGate, *, envelope_ttl_s: float = 5.0) -> None: ...
    def release_skill(self, proposal: SkillProposal) -> tuple[GateDecision, SkillEnvelope | None]: ...
    def filter_chunk(self, chunk: np.ndarray, state: RobotState) -> ChunkResult: ...


@dataclass(frozen=True)
class ChunkResult:
    action: Literal["pass", "clamp", "hold"]
    chunk: np.ndarray  # the chunk to send, or a hold chunk
    violations: tuple[str, ...]
```

## Acceptance criteria

1. The Stage 1 plunge demo, rewritten on `vla_guard`, holds before any motion toward the
   mannequin, and the bread-cutting control case is released.
2. No envelope is ever created from an outcome other than `RELEASED` (tested for each hold
   outcome).
3. `filter_chunk` p99 latency is under 1 ms on the reference chunk shape, reported by a
   benchmark in `benchmarks/`.
4. Expired envelopes and stale perception hold.
5. Offline tests use `tests/fakes.py`; any live test is opt-in and states its call count.

## Open questions

- How the VLA's skill proposal is extracted (an explicit skill head, a second VLM pass, or
  a planner wrapper).
- Whether clamping is ever allowed for force, or force violations always hold.
- Mapping from the payload's scalar bounds to per-link speed limits.
