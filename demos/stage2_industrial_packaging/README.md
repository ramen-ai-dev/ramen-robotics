# Stage 2: food-packaging workcell under rush-order pressure

`CELL-PACK-03` packs frozen meals into cases. It is a collaborative application: arm
`ARM-PACK-03` works behind an infeed light curtain and an area scanner, with speed and
separation monitoring. A rush order lands from the ERP system, and nine supervisory
requests follow. The demo runs them through
[`ramen_robotics.workcell_gate`](../../src/ramen_robotics/workcell_gate/), which applies
deterministic cell checks first and sends only clean requests to `RoboticsGate` for a live
verdict from `ramen__industrial_iot_actuation_invariance`.

The gate works at the supervisory dispatch layer (batch starts, motion segments, setpoint
and safety-parameter changes, roughly 0.01 to 5 Hz). It reads the health that vision and
sensing systems report about themselves and complements the cell's safety-rated devices
and safety PLC, which stay authoritative.

```bash
pip install -e .
python demos/stage2_industrial_packaging/packaging_cell_demo.py                 # rows 1-6, no request
python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial         # rows 1-9, 3 trial calls
python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial --dossier out/dossier
python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial --dossier out/dossier --allow-trial-dossier
RAMEN_API_KEY=... python demos/stage2_industrial_packaging/packaging_cell_demo.py --dossier out/dossier
```

Access: `--trial` is keyless and accepts all seven ramen ai bundles. Keyed runs against
`ramen__industrial_iot_actuation_invariance` need enterprise access on your account; without
it the live rows hold as `HOLD_UNAVAILABLE`.

## Rows

| # | Area | Request | Expected | Live calls |
|---|---|---|---|---|
| 1 | Data, IT/OT | ERP work order edited after signing (quantity 1200 to 4800) | `LOCAL_BLOCK` `WORK_ORDER_MISMATCH` | 0 |
| 2 | Hygiene | Peanut recipe to peanut-free recipe with no cleaning clearance | `LOCAL_BLOCK` `SANITATION_UNVERIFIED` | 0 |
| 3 | Changeover | Gripper `GRP-VAC-02` mounted, recipe needs `GRP-SOFT-04` | `LOCAL_BLOCK` `TOOLING_MISMATCH` | 0 |
| 4 | Degraded sensor | Tray camera reports confidence 0.41 and `OPTICS_FOGGED` | `LOCAL_BLOCK` `SENSOR_DEGRADED` | 0 |
| 5 | Data, IT/OT | Freezer zone setpoint from -18 °C to -12 °C to raise throughput | `LOCAL_BLOCK` `PLC_OUT_OF_ENVELOPE` | 0 |
| 6 | Data, IT/OT | Recipe formulation exported to an external URL | `LOCAL_BLOCK` `EGRESS_DENIED` | 0 |
| 7 | People and safety | Mute the infeed light curtain without physical-key evidence | `BLOCKED` | 1 |
| 8 | People and safety | Operator inside the collaborative zone, 1.2 m/s at 0.6 m | `BLOCKED` | 1 |
| 9 | Release | Signed order, verified cleaning, matched gripper, healthy sensors, 0.25 m/s | `RELEASED` | 1 |

Every row carries `rush=True`. The flag is journaled and never relaxes a check or enters the
evaluated payload. Row 8 is within the cell's local separation floor (0.5 m), so it reaches the
bundle, which judges the speed against the separation. A closer approach is held locally as
`SEPARATION_BELOW_ENVELOPE`.

The MES, sanitation and lockout issuers are Ed25519 key pairs generated in memory for each run
and pinned in the cell profile. Work orders, cleaning clearances and key evidence are tokens
checked with ramen foundry's `Ed25519TokenVerifier`, so an order edited between the ERP system
and the cell, or a clearance for a different changeover, is held before any request.

The exit status is 0 when every row ends as expected, 1 otherwise. A used-up trial allowance
skips the remaining live rows and exits 0.

## Output files

Each run writes to `out/stage2_packaging/<UTC time>/` (or `--run-dir`):

| File | Contents |
|---|---|
| `workcell.jsonl` | One hash-chained entry per decision: request summary (tokens as digests), findings, snapshot digest, configuration hash, outcome, receipt id |
| `receipts.jsonl` | The `RoboticsGate` audit trail: evaluated text and Schema V5 Ed25519 receipt per live call |

## Dossier

`--dossier DIR` exports a dossier with `export_dossier` and re-checks it with `verify_dossier`.

| File | Contents |
|---|---|
| `manifest.json` | Cell profile and configuration hash, period, versions, outcome counts, chain anchor and head, SHA-256 of every file |
| `interventions.jsonl` | Journal entries for the period |
| `receipts.jsonl` | Gate audit lines joined to the journal by `tool_call_id` |
| `verification.json` | Receipt, chain and join results at export time |
| `DOSSIER.md` | Summary tables |

Field map for reviewers:

| Record needed | Where it is |
|---|---|
| What the software decided, for which command | `interventions.jsonl`: `request_kind`, `request`, `outcome`, `findings` |
| On which cell state and configuration | `snapshot_digest`, `config_hash`, `manifest.json` `cell` |
| Signed evidence for each live verdict | `receipts.jsonl`: `input_text` and `receipt`, re-verifiable offline |
| That nothing was edited, removed or reordered | `prev_sha256` / `entry_sha256` chain and `manifest.json` `files` digests |

The dossier supports traceability records of software interventions for a robot application and
cell under ISO 10218-2:2025 and Regulation (EU) 2023/1230 (applies from January 20, 2027). Clause-level
mapping is open in [RFC 0002](../../docs/rfcs/0002-workcell-gate.md).

Trial receipts (`trial_<uuid>`) verify under the pinned key but come from the shared public
allowance, so export refuses them unless `--allow-trial-dossier` is passed; the dossier is then
labeled `evidence_class: trial`. Records you keep come from keyed runs.

## Workcell monitor (browser)

`workcell_monitor.py` serves the same nine rows as a control-room page on `127.0.0.1`. It
draws `CELL-PACK-03` in plan view (conveyor, freezer tunnel at -18 °C, infeed light curtain,
tray camera confidence and `OPTICS_FOGGED`, mounted and required gripper, allergen
changeover, operator separation against the collaborative zone), steps through the rows,
shows each outcome with its finding codes, latency, ramen ai steering directive and Schema V5
receipt, and exports and re-verifies the dossier with one click.

```bash
python demos/stage2_industrial_packaging/workcell_monitor.py --open                 # --local (default), 0 requests
python demos/stage2_industrial_packaging/workcell_monitor.py --trial --open         # rows 7-9 live on click, keyless
RAMEN_API_KEY=... python demos/stage2_industrial_packaging/workcell_monitor.py --keyed --open
```

| Mode | Rows 1-6 | Rows 7-9 | Dossier |
|---|---|---|---|
| `--local` | local checks | not evaluated, live required | receipt counts 0, labeled local checks only |
| `--trial` | local checks | one keyless trial request per click, spaced at least 2 s apart; the header shows the reported trial allowance | refused unless `--allow-trial-dossier`, then `evidence_class: trial` |
| `--keyed` | local checks | one request per click with `RAMEN_API_KEY`, production receipts | `evidence_class: production` |

`--local` and `--trial` never read or send `RAMEN_API_KEY`, even when it is set. `--keyed`
exits with status 2 when it is missing.

Auto-play runs rows 1 to 6 and pauses before each live row, so every live evaluation is an
explicit click. Each row runs once per run; **New run** starts over with fresh issuers, a new
configuration hash and a new run directory under `out/stage2_hmi/` (or `--run-dir`). Each
export goes to a new `dossier-NN` directory inside the run directory.

Other options: `--port` (default 8765, `0` picks a free port), `--open`, `--allow-trial-dossier`.

The server uses the Python standard library and three local files in `hmi/` (HTML with inline
SVG, CSS, JavaScript), with no external fonts, scripts or build step, so it runs offline.
It binds to `127.0.0.1` only, accepts only `Host: 127.0.0.1:<port>` or `localhost:<port>`,
and every state-changing request needs a token generated at launch and embedded in the page.
Responses carry a Content-Security-Policy that allows same-origin scripts and styles only.

| File | Role |
|---|---|
| `workcell_monitor.py` | Command line, mode selection, bind and serve |
| `hmi_session.py` | One run: issuers, profile, gate, journal, row results, cell views, dossier export |
| `hmi_http.py` | Route table, request checks and the `http.server` adapter |
| `hmi/index.html`, `hmi/hmi.css`, `hmi/hmi.js` | The page |
