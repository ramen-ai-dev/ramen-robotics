# RFC 0003: `ros2_bridge`, a goal interceptor ahead of MoveIt 2 and Nav2

| Field | Value |
|---|---|
| Stage | 3 |
| Module | `src/ramen_robotics/ros2_bridge/` |
| Status | Open for a contributor |
| Builds on | `ramen_robotics.gate.RoboticsGate` |
| Targets | ROS 2 (rclpy), MoveIt 2 `move_group`, Nav2, BehaviorTree.CPP |

## Position in the stack

`ros2_bridge` is an outer task-dispatch layer. It decides whether a task-level goal is
forwarded to the motion stack at all. It complements, and never replaces, the hardware
safety chain: safety PLCs, safety-rated stops, speed and separation monitoring and every
other safety function with a rated performance level stay where they are and stay
authoritative.

```
task planner / BT / operator
      |  goal (MoveGroup, FollowJointTrajectory, NavigateToPose, ...)
      v
ros2_bridge interceptor ── RoboticsGate.dispatch(payload) ── https://api.ramenai.dev
      |  RELEASED: forward the goal unchanged
      |  otherwise: reject or abort the goal, publish the GateDecision
      v
move_group / Nav2 ──> controllers ──> robot          safety PLC chain (independent)
```

## Design

Two entry points share one goal-to-payload mapping.

1. **Action goal interceptor.** A proxy action server exposes the same action type and name
   that clients already use (remapped), maps each goal to a `dispatch_manipulation` payload,
   and forwards the goal to the real server only on `Outcome.RELEASED`. Cancel and feedback
   pass through. A hold rejects the goal (`GoalResponse.REJECT`) or aborts it with the
   decision's reason.
2. **BehaviorTree precondition.** A ROS 2 service, `ramen_robotics/CheckDispatch`, that a
   BehaviorTree condition node calls before an action node. It returns the outcome, the
   receipt id and the steering text. A thin BehaviorTree.CPP plugin wraps the service call.

Mapping rules:

- `human_proximity_meters` and `active_hazard_flags` come from perception topics with
  timestamps; stale data maps to a hold before any request.
- Velocity and force bounds come from the goal (for example MoveIt velocity and
  acceleration scaling multiplied by configured maxima).
- One evaluation per goal. Nothing runs inside the controller loop.

```python
class GoalMapper(Protocol):
    def to_payload(self, goal: Any, context: PerceptionSnapshot) -> Mapping[str, Any]: ...


class GoalInterceptor:
    def __init__(
        self,
        node: rclpy.node.Node,
        gate: RoboticsGate,
        mapper: GoalMapper,
        *,
        action_type: type,
        upstream_name: str,
        exposed_name: str,
    ) -> None: ...
```

## Packaging

`rclpy` is not installed from PyPI, so the module imports it lazily and the base package
stays installable without ROS 2. The goal mapping and decision handling are plain Python and
are tested offline. An optional integration test runs in a ROS 2 container and is excluded
from the default CI matrix.

## Acceptance criteria

1. With the fake server, a hazardous goal never reaches the upstream action server and a
   safe goal is forwarded unchanged (tested with fake action interfaces).
2. Stale perception holds with zero requests.
3. Every hold publishes the `GateDecision` (outcome, receipt id, reason) on a diagnostics
   topic.
4. Documentation shows the remapping for `move_group` and for Nav2 `navigate_to_pose`.

## Open questions

- Which ROS 2 distributions to support first.
- Whether to ship the BehaviorTree.CPP plugin here or in a separate repository.
- Payload mapping for navigation goals, which `dispatch_manipulation` does not model; see
  RFC 0004.
