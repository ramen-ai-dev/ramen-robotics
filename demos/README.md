# Demos

Each demo builds on `ramen_robotics.gate` and runs in three modes:

| Mode | How | Live calls |
|---|---|---|
| local | no flag, no `RAMEN_API_KEY` | none; payload contract checks only |
| trial | `--trial`, no `RAMEN_API_KEY` | from the keyless public trial (about 10 per day per network) |
| keyed | `RAMEN_API_KEY` exported | from your account; `--trial` is ignored |

`--audit PATH` appends one JSON line per dispatch, receipts included. Check it later with
`python -c "from ramen_robotics import verify_audit_file; print(verify_audit_file('PATH', allow_trial=True))"`.

| Directory | Demo | Trial calls |
|---|---|---|
| [`00_quickstart/`](00_quickstart/) | `kinematic_gate_demo.py` | 2 |
| [`00_quickstart/`](00_quickstart/) | `asimov_robotics.py` | 2 by default, 1 per case with `--cases` |
| [`stage1_conflicting_goals/`](stage1_conflicting_goals/) | `plunge_demo.py` (MuJoCo) | 1 |
| [`stage2_industrial_packaging/`](stage2_industrial_packaging/) | `packaging_cell_demo.py` (workcell gate, dossier) | 3 |
