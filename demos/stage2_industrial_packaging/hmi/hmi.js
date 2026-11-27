// Stage 2 workcell monitor. Same-origin JSON API only; every server value is written
// with textContent or setAttribute, never parsed as markup.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TOKEN = document.querySelector('meta[name="hmi-token"]').getAttribute("content");
  const SCALE = 110; // SVG pixels per meter around the arm
  const BASE_X = 690;
  const BAR_X = 478;
  const FOCUS = {
    1: "g-workorder", 2: "g-sanitation", 3: "g-gripper", 4: "g-camera", 5: "g-freezer",
    6: "g-egress", 7: "g-curtain", 8: "g-zone", 9: "g-conveyor",
  };
  const CHECK_GROUPS = {
    work_order: "g-workorder", sanitation: "g-sanitation", gripper: "g-gripper", tray_camera: "g-camera",
    freezer: "g-freezer", egress: "g-egress", light_curtain: "g-curtain", separation: "g-zone",
  };
  const STAGES = {
    precheck: "local pre-check, held before any request",
    gate: "live verdict through RoboticsGate",
    local_only: "local only",
  };
  const ICONS = { bad: "!", warn: "!", ok: "\u2713" };
  const state = { session: null, selected: null, cell: null, busy: false, autoplay: false, runningAll: false };

  // -- helpers ---------------------------------------------------------------

  function el(tag, opts, ...children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.cls) node.className = opts.cls;
      if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
      if (opts.title) node.title = opts.title;
    }
    for (const child of children) if (child) node.appendChild(child);
    return node;
  }
  function setText(id, text) { $(id).textContent = text === undefined || text === null ? "" : String(text); }
  function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
  function short(hash, n) { return hash ? String(hash).slice(0, n || 12) + "\u2026" : "-"; }
  function fmt(value, digits) { return value === null || value === undefined ? "-" : Number(value).toFixed(digits === undefined ? 2 : digits); }
  function family(outcome) { return outcome && outcome.startsWith("HOLD") ? "HOLD" : outcome; }
  function rowById(n) { return state.session.rows.find((row) => row.number === n); }
  function expectedText(row) { return row.expected + (row.expected_code ? " " + row.expected_code : ""); }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function setState(id, value) {
    const group = $(id);
    group.classList.remove("state-ok", "state-warn", "state-bad");
    if (value) group.classList.add("state-" + value);
  }

  // Labels arrive with the brand already lowercase ("BLOCKED BY ramen ai"); no CSS upper-cases them.
  function fillLabel(node, label) {
    node.textContent = String(label || "");
    return node;
  }
  function labelChip(label, cls) { return fillLabel(el("span", { cls: "chip " + (cls || "") }), label); }

  async function api(method, path) {
    const init = { method, cache: "no-store", headers: {} };
    if (method === "POST") init.headers["X-HMI-Token"] = TOKEN;
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      return { ok: false, status: 0, body: { error: "offline", detail: "The monitor is not reachable. Is workcell_monitor.py still running?" } };
    }
    let body = {};
    try { body = await response.json(); } catch (error) { body = {}; }
    return { ok: response.ok, status: response.status, body };
  }

  // -- header, layers, summary -----------------------------------------------

  function quotaText(session) {
    if (session.mode !== "trial") return "not used";
    const entries = Object.entries(session.quota || {});
    if (!entries.length) return session.trial_limit_reached ? "used up for today" : "shown after the first live call";
    return entries.map(([key, value]) => {
      if (key === "reset_epoch_seconds" && Number.isFinite(Number(value))) {
        return "reset " + new Date(Number(value) * 1000).toISOString().replace(".000Z", "Z");
      }
      return key + " " + (typeof value === "object" ? JSON.stringify(value) : value);
    }).join(" \u00b7 ");
  }

  function renderSession() {
    const s = state.session;
    const badge = $("mode-badge");
    badge.textContent = s.mode_label;
    badge.className = "badge " + s.mode;
    setText("cell-id", s.cell.cell_id);
    setText("quota", quotaText(s));
    setText("config-hash", short(s.cell.config_hash, 16));
    $("config-hash").title = s.cell.config_hash;
    setText("reached", s.reached.length ? s.reached.length + " (approved only)" : "0");
    $("reached").title = s.reached.join("; ");
    setText("mode-line", s.mode_line);
    setText("run-dir", "Run directory: " + s.run_dir);
    renderLayers();
    renderSummary();
    updateControls();
  }

  function renderLayers() {
    const box = $("layers");
    box.replaceChildren();
    for (const layer of state.session.layers) {
      const list = el("ol", { cls: "row-list" });
      for (const number of layer.rows) list.append(el("li", null, rowButton(rowById(number))));
      box.append(el("section", { cls: "layer layer-" + layer.number },
        el("h3", { cls: "layer-title", text: layer.title }),
        el("p", { cls: "layer-sub", text: layer.subtitle + ". " + layer.detail }),
        list));
    }
  }

  function rowButton(row) {
    const button = el("button", { cls: "row-item" });
    button.type = "button";
    const result = row.result;
    if (result) button.classList.add("s-" + family(result.outcome));
    if (row.number === state.selected) {
      button.classList.add("selected");
      button.setAttribute("aria-current", "true");
    }
    const chips = el("span", { cls: "chips" });
    if (result) {
      chips.append(labelChip(row.label, family(result.outcome)));
      if (result.status === "unexpected") chips.append(el("span", { cls: "chip unexpected", text: "UNEXPECTED" }));
      const codes = [...new Set(result.findings.map((finding) => finding.code))];
      chips.append(el("span", { cls: "chip code", text: [result.outcome, ...codes].join(" \u00b7 ") }));
    } else {
      chips.append(labelChip(row.label, "pending"));
      chips.append(el("span", { cls: "chip code", text: "expected " + expectedText(row) }));
    }
    button.append(
      el("span", { cls: "num", text: row.number }),
      el("span", { cls: "title", text: row.title }),
      el("span", { cls: "step", text: row.area + " \u00b7 " + row.step }),
      chips,
    );
    button.addEventListener("click", () => {
      if (state.autoplay) stopAutoplay("Auto-play stopped.");
      selectRow(row.number);
    });
    return button;
  }

  function renderSummary() {
    const sum = state.session.summary;
    setText("summary-plain", sum.plain);
    const outcomes = Object.entries(sum.by_outcome).map(([key, value]) => key + " " + value).join(", ");
    setText("summary-tech", sum.ran + " of 9 ran \u00b7 " + sum.ok + " as expected \u00b7 " + sum.unexpected +
      " unexpected \u00b7 " + plural(sum.live_requests, "live request") + (outcomes ? " \u00b7 " + outcomes : ""));
  }

  function runAllText(s) {
    const all = s.run_all;
    if (all.pending_rows === 0) return "All 9 steps ran";
    if (all.pending_rows === s.rows.length) return "Run all " + all.pending_rows + " (" + plural(all.pending_live, "live call") + ")";
    return "Run remaining " + all.pending_rows + " (" + plural(all.pending_live, "live call") + ")";
  }

  function updateControls() {
    const s = state.session;
    if (!s) return;
    const row = state.selected ? rowById(state.selected) : null;
    const run = $("btn-run");
    run.classList.toggle("live", Boolean(row && row.live && row.available && !row.result));
    if (!row) {
      run.disabled = true;
      run.textContent = "Select a step";
    } else if (row.result) {
      run.disabled = true;
      run.textContent = "Step " + row.number + " done";
    } else if (!row.available) {
      run.disabled = true;
      run.textContent = "Live evaluation needs --trial or --keyed";
    } else if (row.live && s.trial_limit_reached) {
      run.disabled = true;
      run.textContent = "Trial allowance used up";
    } else {
      run.disabled = state.busy || state.runningAll;
      run.textContent = row.live ? "Evaluate step " + row.number + " live with ramen ai (1 call)" : "Run step " + row.number + " (local check)";
    }
    const auto = $("btn-autoplay");
    auto.textContent = state.autoplay ? "Stop auto-play" : "Auto-play";
    auto.classList.toggle("active", state.autoplay);
    auto.disabled = state.runningAll || (state.busy && !state.autoplay);
    $("btn-step").disabled = state.busy || state.autoplay || state.runningAll;
    $("btn-reset").disabled = state.busy || state.runningAll;
    $("btn-export").disabled = state.busy || state.runningAll || s.summary.ran === 0;

    const runAll = $("btn-run-all");
    runAll.classList.toggle("running", state.runningAll);
    if (state.runningAll) {
      runAll.disabled = false;
      runAll.textContent = "Stop after this step";
    } else {
      runAll.disabled = !s.run_all.enabled || s.run_all.pending_rows === 0 || state.busy || state.autoplay;
      runAll.textContent = runAllText(s);
    }
    const notes = {
      local: "Live calls need --trial or --keyed.",
      trial: "Runs every step in order. Live calls go to the keyless trial, at least 2 s apart.",
      keyed: "Runs every step in order. Live calls use RAMEN_API_KEY and return production receipts.",
    };
    setText("run-all-note", s.trial_limit_reached ? "The trial allowance is used up for today." : notes[s.mode]);
  }

  // -- story card ------------------------------------------------------------

  function renderStory() {
    const c = state.cell;
    const st = c.story;
    const result = rowById(c.number).result || c.result;
    $("story").className = "story " + st.tone + (st.safe ? " safe" : "");
    setText("story-step", "Step " + c.number + " of 9 \u00b7 Layer " + c.layer);
    setText("story-title", st.title);
    setText("story-attempt", st.attempt);
    setText("story-risk", st.risk);
    setText("story-response", st.response);
    const codes = result ? [...new Set(result.findings.map((finding) => finding.code))] : [];
    setText("story-tech", [c.request_kind + (c.rush ? " (rush)" : ""), st.technical, result ? result.outcome : null, ...codes]
      .filter(Boolean).join(" \u00b7 "));
  }

  // -- schematic -------------------------------------------------------------

  function renderCell() {
    const c = state.cell;
    const result = rowById(c.number).result || c.result;
    const statuses = Object.fromEntries(c.checks.map((check) => [check.id, check.status]));
    for (const [n, id] of Object.entries(FOCUS)) $(id).classList.toggle("focus", Number(n) === c.number);
    for (const [check, group] of Object.entries(CHECK_GROUPS)) setState(group, statuses[check] || null);

    const wo = c.work_order;
    if (wo) {
      const changed = wo.signed_quantity !== null && wo.signed_quantity !== undefined && wo.signed_quantity !== wo.requested_quantity;
      setText("wo-qty", changed ? "approved " + Number(wo.signed_quantity).toLocaleString("en-US") + ", asking " +
        Number(wo.requested_quantity).toLocaleString("en-US") : Number(wo.requested_quantity).toLocaleString("en-US") + " meals, signed");
      setText("wo-id", wo.work_order_id + " \u00b7 " + wo.lot_id);
    } else {
      setText("wo-qty", "batch already running");
      setText("wo-id", c.request_kind);
      setState("g-workorder", null);
    }

    const san = c.sanitation;
    const removed = san.previous_allergens.filter((item) => !san.next_allergens.includes(item));
    setText("san-change", removed.length ? removed.join(", ") + " \u2192 " + removed.join(", ") + "-free" : "no allergen change");
    setText("san-state", san.clearance_presented ? "cleaning signed off" : "no cleaning sign-off");
    setText("san-allergens", (san.previous_recipe_id || "-") + " \u2192 " + san.next_recipe_id);

    const grip = c.gripper;
    setText("grip-mounted", grip.match ? "correct gripper fitted" : "wrong gripper fitted");
    setText("grip-required", grip.mounted + ", recipe needs " + (grip.required.join(", ") || "-"));

    const cam = c.sensors.tray_camera_infeed;
    if (cam) {
      const width = Math.max(0, Math.min(1, Number(cam.confidence))) * 100;
      $("cam-bar").setAttribute("width", String(width));
      const tick = String(BAR_X + Number(c.sensor_min_confidence) * 100);
      $("cam-threshold").setAttribute("x1", tick);
      $("cam-threshold").setAttribute("x2", tick);
      setText("cam-conf", Math.round(Number(cam.confidence) * 100) + "% confident");
      setText("cam-fault", cam.faults.includes("OPTICS_FOGGED") ? "lens fogged" : cam.faults.join(", "));
    }

    const frz = c.freezer;
    setText("frz-temp", fmt(frz.current_c, 0) + " \u00b0C");
    setText("frz-req", frz.requested_c !== null ? "asked for " + fmt(frz.requested_c, 0) + " \u00b0C" : "approved " + fmt(frz.min_c, 0) + " to " + fmt(frz.max_c, 0) + " \u00b0C");
    setText("frz-env", frz.node_id + " " + frz.tag);

    const eg = c.egress;
    if (eg.requested) {
      let host = eg.destination;
      try { host = new URL(eg.destination).host || eg.destination; } catch (error) { host = eg.destination; }
      setText("egress-state", eg.allowed ? "export allowed" : "leaving the company");
      setText("egress-dest", host);
    } else {
      setText("egress-state", "stays inside the plant");
      setText("egress-dest", (eg.allowed_destinations.join(", ") || "none") + " only");
      setState("g-egress", null);
    }

    const lc = c.light_curtain;
    setText("lc-state", lc.mute_requested ? "switch-off requested" : lc.safeguard_ok ? "on" : "fault");

    renderSeparation(c);

    const fam = result ? family(result.outcome) : null;
    $("trays").classList.toggle("running", fam === "RELEASED");
    setText("conveyor-state", fam === "RELEASED" ? "Line running: batch approved and started" : result ? "Line paused: command not executed" : "Line paused (meals flow left to right)");
    $("lamp-red").classList.toggle("on", fam === "BLOCKED");
    $("lamp-amber").classList.toggle("on", fam === "LOCAL_BLOCK" || fam === "HOLD");
    $("lamp-green").classList.toggle("on", fam === "RELEASED");
    fillLabel($("stack-text"), result ? rowById(c.number).label : "idle");
    renderSpotlight();
  }

  function renderSeparation(c) {
    const sep = c.separation;
    const zoneR = Number(sep.collaborative_zone_m) * SCALE;
    const floorR = Number(sep.min_separation_m || 0) * SCALE;
    $("zone-ring").setAttribute("r", String(zoneR));
    $("floor-ring").setAttribute("r", String(floorR));
    $("zone-label").setAttribute("y", String(210 - zoneR + 14));
    $("floor-label").setAttribute("y", String(210 - floorR + 13));
    setText("zone-label", "shared work zone " + fmt(sep.collaborative_zone_m, 1) + " m");
    setText("floor-label", floorR > 20 ? "minimum " + fmt(sep.min_separation_m, 1) + " m" : "");
    const speed = sep.commanded_speed_mps;
    setText("speed-text", speed !== null && speed !== undefined ? "arm " + fmt(speed) + " m/s" : "arm idle");
    const operator = $("g-operator");
    if (sep.nearest_human_m === null || sep.nearest_human_m === undefined) {
      operator.classList.add("hidden");
      $("sep-line").classList.add("hidden");
      return;
    }
    const d = Number(sep.nearest_human_m);
    const x = BASE_X + Math.min(d, 2.5) * SCALE;
    operator.classList.remove("hidden");
    operator.setAttribute("transform", "translate(" + x + " 210)");
    $("sep-line").classList.remove("hidden");
    $("sep-line").setAttribute("x2", String(x - 20));
    const label = $("op-label");
    label.setAttribute("text-anchor", x > 930 ? "end" : "middle");
    label.setAttribute("x", x > 930 ? "20" : "0");
    setText("op-label", "operator " + fmt(d, 1) + " m");
  }

  function renderSpotlight() {
    const spot = state.cell.spotlight;
    const box = $("spotlight");
    box.replaceChildren();
    if (spot.card) {
      const card = spot.card;
      const kicker = card.status === "bad" ? card.name + ": fault found" : card.name + ": needs a live safety decision";
      box.append(el("div", { cls: "spot " + card.status },
        el("span", { cls: "icon", text: ICONS[card.status] || "!" }),
        el("p", { cls: "spot-kicker", text: kicker }),
        el("p", { cls: "spot-text", text: card.summary }),
        el("p", { cls: "tech", text: card.technical })));
    } else {
      box.append(el("div", { cls: "spot ok" },
        el("span", { cls: "icon", text: ICONS.ok }),
        el("p", { cls: "spot-kicker", text: "All clear" }),
        el("p", { cls: "spot-text", text: "Every factory check passed for this step." }),
        el("p", { cls: "tech", text: state.cell.checks.length + " subsystems healthy" })));
    }
    const details = $("other-checks");
    details.classList.toggle("attention", spot.others.some((item) => item.status !== "ok"));
    setText("other-summary", spot.others_summary);
    const list = $("other-list");
    list.replaceChildren();
    for (const item of spot.others) {
      list.append(el("li", { cls: item.status },
        el("span", { cls: "name", text: item.name + ": " }),
        el("span", { text: item.summary }),
        el("p", { cls: "tech", text: item.technical })));
    }
  }

  // -- decision panel --------------------------------------------------------

  function renderDecision() {
    const c = state.cell;
    const row = rowById(c.number);
    const result = row.result || c.result;
    const box = $("outcome");
    box.className = "outcome " + (result ? family(result.outcome) : "pending");
    fillLabel($("outcome-text"), row.label);
    if (result) {
      const codes = [...new Set(result.findings.map((finding) => finding.code))];
      const verdict = result.status === "ok" ? "\u2713 as expected" : result.status === "limit" ? "trial allowance used up" : "UNEXPECTED, expected " + expectedText(row);
      setText("outcome-tech", [result.outcome, ...codes, verdict].join(" \u00b7 "));
    } else {
      setText("outcome-tech", "expected " + expectedText(row));
    }
    setText("d-request", c.request_kind + (c.rush ? " (rush order)" : ""));
    setText("d-stage", result ? STAGES[result.stage] || result.stage : "-");
    setText("d-latency", result ? (result.gate ? fmt(result.gate.latency_ms, 0) + " ms round trip" : fmt(result.elapsed_ms, 1) + " ms local check") : "-");
    setText("d-calls", result ? (result.stage === "gate" ? "1 evaluation request" : "0, held before any request") :
      row.live ? (row.available ? "1 when you click" : "0 in local mode") : "0");
    setText("d-callid", result ? result.tool_call_id : "-");

    const findings = $("d-findings");
    findings.replaceChildren();
    if (result && result.findings.length) {
      for (const finding of result.findings) {
        findings.append(el("li", null, el("span", { cls: "code", text: finding.code }), el("span", { text: finding.detail })));
      }
    } else {
      findings.append(el("li", { cls: "muted", text: result ? (result.stage === "gate" ? "none: every factory check passed, sent for a live verdict" : "none") : "-" }));
    }

    const steering = $("d-steering");
    const directive = result && result.gate && result.gate.steering ? result.gate.steering : result && result.stage === "gate" ? result.reason : null;
    steering.textContent = directive || (result && result.gate ? "None: the request was approved." :
      result ? "None needed: stopped locally before any request." : "-");
    steering.classList.toggle("muted", !directive);

    const receipt = $("d-receipt");
    receipt.replaceChildren();
    const add = (label, value, cls) => { receipt.append(el("dt", { text: label }), el("dd", { cls: cls || "", text: value })); };
    if (result && result.gate && result.gate.receipt_id) {
      const g = result.gate;
      add("Signature", g.receipt_verified ? "\u2713 verified (Ed25519, Schema V5)" : "\u2717 did not verify", g.receipt_verified ? "ok-text" : "bad-text");
      add("Kind", g.receipt_class === "trial" ? "trial receipt (public allowance)" : g.receipt_class === "production" ? "production receipt" : "-");
      add("Machine ran it", result.executed ? "yes, after approval" : "no", result.executed ? "ok-text" : "");
      add("Receipt id", g.receipt_id, "mono");
      add("Key", g.kid || "-", "mono");
      add("Verdict", g.verdict || "-", "mono");
    } else if (result) {
      add("Receipt", result.stage === "gate" ? "none returned" : "Not needed: stopped locally, nothing was sent.", "muted");
    } else {
      add("Receipt", row.live ? "Issued by ramen ai when this step runs live." : "Not needed for local checks.", "muted");
    }
  }

  // -- actions ---------------------------------------------------------------

  async function refreshSession() {
    const res = await api("GET", "/api/session");
    if (res.ok) {
      state.session = res.body;
      renderSession();
    }
    return res.ok;
  }

  async function refreshAll() {
    await refreshSession();
    await Promise.all([refreshJournal(), refreshReceipts()]);
  }

  async function selectRow(n) {
    state.selected = n;
    const res = await api("GET", "/api/rows/" + n + "/cell");
    if (!res.ok) {
      setText("run-message", res.body.detail || "could not load step " + n);
      return;
    }
    if (state.selected !== n) return;
    state.cell = res.body;
    renderLayers();
    renderStory();
    renderCell();
    renderDecision();
    updateControls();
  }

  async function runRow(n) {
    if (state.busy) return false;
    const row = rowById(n);
    state.busy = true;
    updateControls();
    setText("run-message", row.live ? (state.session.mode === "trial" ?
      "Asking ramen ai on the keyless trial. Calls are spaced at least 2 s apart." : "Asking ramen ai with RAMEN_API_KEY.") : "Running local factory checks.");
    const res = await api("POST", "/api/rows/" + n + "/run");
    state.busy = false;
    setText("run-message", res.ok ? "" : res.body.detail || "request failed (" + res.status + ")");
    await refreshAll();
    if (state.selected === n) await selectRow(n);
    return res.ok;
  }

  function stopAutoplay(message) {
    state.autoplay = false;
    if (message !== undefined) setText("autoplay-note", message);
    updateControls();
  }

  async function continueAutoplay() {
    while (state.autoplay) {
      const next = state.session.rows.find((row) => !row.result);
      if (!next) {
        stopAutoplay("All nine steps ran. Export the audit dossier below.");
        return;
      }
      await selectRow(next.number);
      if (next.live) {
        const pending = state.session.rows.filter((row) => row.live && !row.result).map((row) => row.number);
        if (!next.available) {
          stopAutoplay("Steps " + pending.join(", ") + " need a live ramen ai evaluation. Restart with --trial or --keyed to run them.");
        } else if (state.session.trial_limit_reached) {
          stopAutoplay("The trial allowance is used up for today.");
        } else {
          setText("autoplay-note", "Paused before live step " + next.number + ". Click \u201cEvaluate step " + next.number + " live\u201d to send 1 call.");
          updateControls();
        }
        return;
      }
      await sleep(1100);
      if (!state.autoplay) return;
      if (!(await runRow(next.number))) {
        stopAutoplay("Auto-play stopped on an error.");
        return;
      }
      await sleep(1700);
    }
  }

  async function toggleAutoplay() {
    if (state.autoplay) {
      stopAutoplay("Auto-play stopped.");
      return;
    }
    state.autoplay = true;
    setText("autoplay-note", "Auto-play: local steps run in order and pause before every live step.");
    updateControls();
    await continueAutoplay();
  }

  async function runAll() {
    if (state.runningAll) {
      state.runningAll = false;
      setText("autoplay-note", "Run all will stop after the current step.");
      updateControls();
      return;
    }
    if (!state.session.run_all.enabled || state.busy) return;
    state.runningAll = true;
    setText("autoplay-note", "Running every step in order, including the live calls.");
    updateControls();
    for (let n = 1; n <= state.session.rows.length && state.runningAll; n += 1) {
      const row = rowById(n);
      if (row.result) continue;
      if (row.live && state.session.trial_limit_reached) {
        setText("autoplay-note", "The trial allowance is used up for today; the remaining live steps were not sent.");
        break;
      }
      await selectRow(n);
      await sleep(row.live ? 900 : 1100);
      if (!state.runningAll) break;
      if (!(await runRow(n))) {
        setText("autoplay-note", "Run all stopped on an error at step " + n + ".");
        break;
      }
      await sleep(row.live ? 2400 : 1700);
    }
    if (state.runningAll && !state.session.rows.some((row) => !row.result)) {
      setText("autoplay-note", "All nine steps ran. Export the audit dossier below.");
    }
    state.runningAll = false;
    updateControls();
  }

  async function step() {
    const next = state.session.rows.find((row) => !row.result);
    if (!next) {
      setText("autoplay-note", "All nine steps ran.");
      return;
    }
    await selectRow(next.number);
    if (next.live) {
      setText("autoplay-note", next.available ? "Step " + next.number + " is live: click \u201cEvaluate step " + next.number + " live\u201d to send 1 call." :
        "Step " + next.number + " needs a live ramen ai evaluation. Restart with --trial or --keyed.");
      return;
    }
    await runRow(next.number);
  }

  async function runSelected() {
    if (!state.selected) return;
    await runRow(state.selected);
    if (state.autoplay) await continueAutoplay();
  }

  async function resetRun() {
    stopAutoplay("");
    state.busy = true;
    updateControls();
    const res = await api("POST", "/api/reset");
    state.busy = false;
    if (!res.ok) {
      setText("autoplay-note", res.body.detail || "could not start a new run");
      updateControls();
      return;
    }
    $("export-result").replaceChildren();
    $("export-md").hidden = true;
    await refreshAll();
    await selectRow(1);
    setText("autoplay-note", "New run: fresh signing keys, configuration hash, decision log and receipts.");
  }

  // -- audit trail -----------------------------------------------------------

  function cell(text, cls) { return el("td", { cls: cls || "", text: text === null || text === undefined ? "-" : text }); }
  function resultCell(label, outcome) {
    return el("td", null, labelChip(label, family(outcome)), el("p", { cls: "tech", text: outcome }));
  }

  async function refreshJournal() {
    const res = await api("GET", "/api/journal");
    if (!res.ok) return;
    const journal = res.body;
    const body = $("journal-body");
    body.replaceChildren();
    for (const entry of journal.entries) {
      const detail = entry.codes.length ? entry.codes.join(", ") : entry.receipt_id ? entry.receipt_id + " (" + entry.receipt_class + ")" : "-";
      const prev = cell(short(entry.prev_sha256, 12), "mono");
      prev.title = entry.prev_sha256;
      const own = cell(short(entry.entry_sha256, 12), "mono");
      own.title = entry.entry_sha256;
      body.append(el("tr", null,
        cell(entry.seq, "mono"),
        cell(entry.timestamp ? entry.timestamp.replace("T", " ").replace("+00:00", "") : "-", "mono"),
        el("td", null, el("span", { text: entry.request_kind + (entry.rush ? " (rush)" : "") }), el("p", { cls: "tech", text: entry.stage })),
        resultCell(entry.label, entry.outcome),
        cell(detail, "mono"),
        prev,
        own,
        cell(entry.linked ? "\u2713 linked" : "\u2717 broken", entry.linked ? "ok-text" : "bad-text"),
      ));
    }
    const status = $("chain-status");
    if (!journal.entries.length) {
      status.textContent = "No entries yet. Every decision, stopped or approved, is recorded here.";
      status.className = "chain-status muted";
    } else if (journal.chain_ok) {
      status.textContent = "\u2713 Chain intact: " + plural(journal.entries.length, "decision") + " recorded, none edited, removed or reordered. " +
        "Head " + short(journal.head, 16) + ".";
      status.className = "chain-status ok-text";
    } else {
      status.textContent = "\u2717 Chain broken: " + journal.problems.join("; ");
      status.className = "chain-status bad-text";
    }
  }

  async function refreshReceipts() {
    const res = await api("GET", "/api/receipts");
    if (!res.ok) return;
    const data = res.body;
    const body = $("receipt-body");
    body.replaceChildren();
    const labels = state.session ? state.session.outcome_labels : {};
    for (const item of data.receipts) {
      const text = el("details", null, el("summary", { text: "show" }), el("pre", { text: item.input_text || "" }));
      body.append(el("tr", null,
        cell(item.receipt_id, "mono"),
        cell(item.kid, "mono"),
        cell(item.schema_version ? "V" + String(item.schema_version).split(".")[0] : "-"),
        cell(item.receipt_class),
        cell(item.verdict),
        resultCell(labels[item.outcome] || item.outcome, item.outcome),
        cell(item.verified ? "\u2713 signature and payload hash" : "\u2717 " + (item.problem || "did not verify"), item.verified ? "ok-text" : "bad-text"),
        el("td", null, text),
      ));
    }
    const status = $("receipt-status");
    if (!data.receipts.length) {
      status.textContent = state.session && state.session.mode === "local" ?
        "No live evaluations in the offline demo. Steps 7 to 9 run live with --trial or --keyed." : "No live evaluations in this run yet.";
      status.className = "chain-status muted";
    } else {
      const kids = [...new Set(data.receipts.map((item) => item.kid).filter(Boolean))].join(", ");
      status.textContent = (data.all_verified ? "\u2713 " : "\u2717 ") + plural(data.receipts.length, "signed receipt") + " (" + data.production + " production, " +
        data.trial + " trial), " + (data.all_verified ? "all re-checked on this machine under " + kids + "." : "some did not verify.");
      status.className = "chain-status " + (data.all_verified ? "ok-text" : "bad-text");
    }
  }

  function renderExport(res) {
    const box = $("export-result");
    const markdown = $("export-md");
    box.replaceChildren();
    if (!res.ok) {
      box.append(el("p", { cls: "export-headline bad-text", text: res.body.error === "dossier_refused" ? "Export refused." : res.body.detail || "Export failed." }));
      if (Array.isArray(res.body.problems)) {
        const list = el("ul");
        for (const problem of res.body.problems) list.append(el("li", { cls: "tech", text: problem }));
        box.append(list);
      }
      if (res.body.hint) box.append(el("p", { cls: "note", text: res.body.hint }));
      markdown.hidden = true;
      return;
    }
    const d = res.body;
    box.append(el("p", { cls: "export-headline " + (d.intact ? "ok-text" : "bad-text"),
      text: d.intact ? "\u2713 Audit dossier exported and verified: nothing missing, nothing altered." : "\u2717 The exported dossier did not verify." }));
    const facts = el("dl");
    const add = (label, value, cls) => { facts.append(el("dt", { text: label }), el("dd", { cls: cls || "", text: value })); };
    add("verify_dossier", d.intact ? "intact: file digests, hash chain, journal-to-receipt join and every receipt re-verified" : d.verify.join("; "), d.intact ? "ok-text" : "bad-text");
    add("Evidence", d.evidence_class ? "evidence_class: " + d.evidence_class : d.evidence_label, d.evidence_class === "trial" ? "warn-text" : "");
    add("Receipts", d.receipts.production + " production, " + d.receipts.trial + " trial");
    add("Decisions", d.decisions + " (" + Object.entries(d.by_outcome).map(([key, value]) => key + " " + value).join(", ") + ")");
    add("Directory", d.path, "mono");
    add("Chain head", d.chain_head, "mono");
    for (const [name, digest] of Object.entries(d.files)) add(name, "sha256 " + digest, "mono");
    box.append(facts);
    markdown.textContent = d.markdown;
    markdown.hidden = false;
  }

  async function exportDossier() {
    if (state.busy) return;
    state.busy = true;
    updateControls();
    const res = await api("POST", "/api/dossier/export");
    state.busy = false;
    renderExport(res);
    await refreshAll();
  }

  function selectTab(name) {
    for (const tab of ["journal", "receipts", "export"]) {
      $("tab-" + tab).setAttribute("aria-selected", String(tab === name));
      $("panel-" + tab).hidden = tab !== name;
    }
  }

  // -- start -----------------------------------------------------------------

  async function init() {
    $("btn-autoplay").addEventListener("click", toggleAutoplay);
    $("btn-step").addEventListener("click", step);
    $("btn-run-all").addEventListener("click", runAll);
    $("btn-reset").addEventListener("click", resetRun);
    $("btn-run").addEventListener("click", runSelected);
    $("btn-export").addEventListener("click", exportDossier);
    for (const tab of ["journal", "receipts", "export"]) $("tab-" + tab).addEventListener("click", () => selectTab(tab));
    if (!(await refreshSession())) {
      setText("mode-line", "The monitor is not reachable. Restart workcell_monitor.py and reload.");
      return;
    }
    const notes = {
      local: "Offline demo: steps 1 to 6 run on this machine with no calls; steps 7 to 9 need a live ramen ai evaluation.",
      trial: "Live demo on the keyless trial: steps 7 to 9 each send one call to ramen ai when you click.",
      keyed: "Live demo with a production key: steps 7 to 9 each send one call and return production receipts.",
    };
    setText("autoplay-note", notes[state.session.mode] || "");
    const first = state.session.rows.find((row) => !row.result);
    await selectRow(first ? first.number : 1);
    await Promise.all([refreshJournal(), refreshReceipts()]);
    const dossier = await api("GET", "/api/dossier");
    if (dossier.ok && dossier.body.exported) renderExport(dossier);
  }

  init();
})();
