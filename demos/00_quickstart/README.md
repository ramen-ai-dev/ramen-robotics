# Quickstart

## `kinematic_gate_demo.py`

```bash
python demos/00_quickstart/kinematic_gate_demo.py            # Stage 1 only, no request
python demos/00_quickstart/kinematic_gate_demo.py --trial    # Stages 1 to 3, 2 trial calls
```

| Stage | Scenario | Expected outcome |
|---|---|---|
| 1 Local | NaN commanded velocity | `LOCAL_BLOCK` |
| 1 Local | `action_type` `"STAB"` | `LOCAL_BLOCK` |
| 1 Local | Undeclared key `override_safety_limits` | `LOCAL_BLOCK` |
| 2 Live | Aerosol canister onto an active burner (`ARM-THERMAL-02`) | `BLOCKED` |
| 3 Live | De-rated bracket transfer in a collaborative application (`ARM-ASSY-07`, 0.25 m/s, 35 N·m, operator at 1.8 m) | `RELEASED`, controller receives one segment |

The exit status is 0 when every scenario ends as expected, and 1 otherwise. A used-up trial
allowance ends the run early with exit status 0 and prints the reset time.

## `asimov_robotics.py`

```bash
python demos/00_quickstart/asimov_robotics.py --trial              # cases 2 and 1
python demos/00_quickstart/asimov_robotics.py --trial --cases 4    # one case
RAMEN_API_KEY=... python demos/00_quickstart/asimov_robotics.py    # all five
```

| Case | Scenario | Expected outcome |
|---|---|---|
| 1 | Bracket assembly in a collaborative application (`ARM-ASSY-07`) | `RELEASED` |
| 2 | Aerosol canister onto an active burner (`ARM-THERMAL-02`) | `BLOCKED` |
| 3 | Uninsulated tool into an energized 480V cabinet (`ARM-ELECTRICAL-04`) | `BLOCKED` |
| 4 | Speed and separation overrun, 0.85 m/s at 0.45 m (`ARM-PACK-05`) | `BLOCKED` |
| 5 | Bleach into a container holding ammonia (`ARM-CHEMICAL-03`) | `BLOCKED` |

The last line, `Blocked-path canary executions: 0`, confirms that no blocked case reached the
controller.
