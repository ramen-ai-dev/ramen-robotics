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
  const STAGES = {
    precheck: "local pre-check, held before any request",
    gate: "live verdict through RoboticsGate",
    local_only: "local only",
  };
  const state = { session: null, selected: null, cell: null, busy: false, autoplay: false };

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
  function setState(id, value) {
    const group = $(id);
    group.classList.remove("state-ok", "state-warn", "state-bad");
    if (value) group.classList.add("state-" + value);
  }

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

  // -- header, rows, summary -------------------------------------------------

  function quotaText(session) {
    if (session.mode !== "trial") return "not used";
    const entries = Object.entries(session.quota || {});
    if (!entries.length) return session.trial_limit_reached ? "used up for today" : "reported after the first live request";
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
    badge.textContent = s.mode.toUpperCase();
    badge.className = "badge " + s.mode;
    setText("cell-id", s.cell.cell_id);
    setText("endpoint", s.endpoint || "none, local checks only");
    setText("quota", quotaText(s));
    setText("config-hash", short(s.cell.config_hash, 16));
    $("config-hash").title = s.cell.config_hash;
    setText("reached", s.reached.length ? s.reached.length + " (" + s.reached.join("; ") + ")" : "0");
    setText("mode-line", s.mode_line);
    setText("run-dir", "Run directory: " + s.run_dir);
    renderRows();
    renderSummary();
    updateControls();
  }

  function renderRows() {
    const list = $("row-list");
    list.replaceChildren();
    for (const row of state.session.rows) {
      const button = el("button", { cls: "row-item" });
      button.type = "button";
      const result = row.result;
      if (result) button.classList.add("s-" + family(result.outcome));
      if (row.number === state.selected) {
        button.classList.add("selected");
        button.setAttribute("aria-current", "true");
      }
      button.append(
        el("span", { cls: "num", text: row.number }),
        el("span", { cls: "area", text: row.area }),
        el("span", { cls: "step", text: row.step }),
      );
      const chips = el("span", { cls: "chips" });
      if (result) {
        chips.append(el("span", { cls: "chip " + family(result.outcome), text: result.outcome }));
        if (result.status === "unexpected") chips.append(el("span", { cls: "chip unexpected", text: "unexpected" }));
        for (const code of new Set(result.findings.map((finding) => finding.code))) {
          chips.append(el("span", { cls: "chip", text: code }));
        }
        if (result.gate && result.gate.receipt_class) {
          chips.append(el("span", { cls: "chip live", text: result.gate.receipt_class + " receipt" }));
        }
      } else {
        chips.append(el("span", { cls: "chip expected", text: "expected " + expectedText(row) }));
        chips.append(el("span", { cls: "chip " + (row.live && row.available ? "live" : "pending"), text: row.note }));
      }
      button.append(chips);
      button.addEventListener("click", () => {
        if (state.autoplay) stopAutoplay("Auto-play stopped.");
        selectRow(row.number);
      });
      list.append(el("li", null, button));
    }
  }

  function renderSummary() {
    const sum = state.session.summary;
    const outcomes = Object.entries(sum.by_outcome).map(([key, value]) => key + " " + value).join(", ");
    setText("summary", sum.ran + " of 9 ran \u00b7 " + sum.ok + " as expected \u00b7 " + sum.unexpected +
      " unexpected \u00b7 " + sum.live_requests + " live requests" + (outcomes ? " \u00b7 " + outcomes : ""));
  }

  function updateControls() {
    const s = state.session;
    if (!s) return;
    const row = state.selected ? rowById(state.selected) : null;
    const run = $("btn-run");
    run.classList.toggle("live", Boolean(row && row.live && row.available && !row.result));
    if (!row) {
      run.disabled = true;
      run.textContent = "Select a request";
    } else if (row.result) {
      run.disabled = true;
      run.textContent = "Row " + row.number + " ran: " + row.result.outcome;
    } else if (!row.available) {
      run.disabled = true;
      run.textContent = "Live verdict needs --trial or --keyed";
    } else if (row.live && s.trial_limit_reached) {
      run.disabled = true;
      run.textContent = "Trial allowance used up";
    } else {
      run.disabled = state.busy;
      run.textContent = row.live ? "Evaluate row " + row.number + " live (1 request)" : "Run row " + row.number + " (local check)";
    }
    const auto = $("btn-autoplay");
    auto.textContent = state.autoplay ? "Stop auto-play" : "Auto-play";
    auto.classList.toggle("active", state.autoplay);
    auto.disabled = state.busy && !state.autoplay;
    $("btn-step").disabled = state.busy || state.autoplay;
    $("btn-reset").disabled = state.busy;
    $("btn-export").disabled = state.busy || s.summary.ran === 0;
  }

  // -- schematic -------------------------------------------------------------

  function renderCell() {
    const c = state.cell;
    const row = rowById(c.number);
    const result = row.result || c.result;
    const reads = [];
    for (const [n, id] of Object.entries(FOCUS)) $(id).classList.toggle("focus", Number(n) === c.number);

    const wo = c.work_order;
    if (wo) {
      const mismatch = wo.signed_quantity !== null && wo.signed_quantity !== undefined && wo.signed_quantity !== wo.requested_quantity;
      setText("wo-id", wo.work_order_id);
      setText("wo-qty", "signed qty " + (wo.signed_quantity ?? "-") + " \u00b7 requested " + wo.requested_quantity);
      setState("g-workorder", mismatch ? "bad" : "ok");
      reads.push(["Work order", wo.work_order_id + ": signed " + (wo.signed_quantity ?? "-") + ", requested " + wo.requested_quantity, mismatch ? "bad" : "ok"]);
    } else {
      setText("wo-id", "batch running");
      setText("wo-qty", c.request_kind + " request");
      setState("g-workorder", null);
      reads.push(["Work order", "no batch start in this request", null]);
    }

    const san = c.sanitation;
    const removed = san.previous_allergens.filter((item) => !san.next_allergens.includes(item));
    setText("san-change", (san.previous_recipe_id || "-") + " \u2192 " + san.next_recipe_id);
    setText("san-allergens", (san.previous_allergens.join(", ") || "-") + " \u2192 " + (san.next_allergens.join(", ") || "-"));
    setText("san-state", san.clearance_presented ? "cleaning clearance presented" : "no cleaning clearance");
    setState("g-sanitation", san.clearance_presented ? "ok" : "bad");
    reads.push(["Allergen sanitation", (san.clearance_presented ? "signed clearance presented" : "no cleaning clearance") +
      (removed.length ? " (" + removed.join(", ") + " removed)" : ""), san.clearance_presented ? "ok" : "bad"]);

    const grip = c.gripper;
    setText("grip-mounted", "mounted " + grip.mounted);
    setText("grip-required", "recipe " + grip.recipe_id + " requires " + (grip.required.join(", ") || "-"));
    setState("g-gripper", grip.match ? "ok" : "bad");
    reads.push(["Gripper", grip.mounted + (grip.match ? " matches " : " mounted, recipe needs ") + (grip.required.join(", ") || "-"), grip.match ? "ok" : "bad"]);

    const cam = c.sensors.tray_camera_infeed;
    if (cam) {
      const width = Math.max(0, Math.min(1, Number(cam.confidence))) * 100;
      $("cam-bar").setAttribute("width", String(width));
      const tick = String(BAR_X + Number(c.sensor_min_confidence) * 100);
      $("cam-threshold").setAttribute("x1", tick);
      $("cam-threshold").setAttribute("x2", tick);
      setText("cam-conf", "confidence " + fmt(cam.confidence));
      setText("cam-fault", cam.faults.join(", "));
      setState("g-camera", cam.healthy ? "ok" : "bad");
      reads.push(["Tray camera", "confidence " + fmt(cam.confidence) + " (min " + fmt(c.sensor_min_confidence) + ")" + (cam.faults.length ? ", " + cam.faults.join(", ") : ", healthy"), cam.healthy ? "ok" : "bad"]);
    }
    const scanner = c.sensors.area_scanner_cell_03;
    if (scanner) reads.push(["Area scanner", "confidence " + fmt(scanner.confidence) + (scanner.healthy ? ", healthy" : ", degraded"), scanner.healthy ? "ok" : "bad"]);

    const frz = c.freezer;
    const outside = frz.requested_c !== null &&
      ((frz.max_c !== null && frz.requested_c > frz.max_c) || (frz.min_c !== null && frz.requested_c < frz.min_c));
    setText("frz-temp", fmt(frz.current_c, 1) + " \u00b0C");
    setText("frz-req", frz.requested_c !== null ? "requested " + fmt(frz.requested_c, 1) + " \u00b0C" : "envelope " + fmt(frz.min_c, 0) + " to " + fmt(frz.max_c, 0) + " \u00b0C");
    setText("frz-env", frz.node_id + " " + frz.tag);
    setState("g-freezer", outside ? "bad" : "ok");
    reads.push(["Freezer zone setpoint", fmt(frz.current_c, 1) + " \u00b0C" + (frz.requested_c !== null ? " \u2192 " + fmt(frz.requested_c, 1) + " \u00b0C requested" : "") +
      " (envelope " + fmt(frz.min_c, 0) + " to " + fmt(frz.max_c, 0) + " \u00b0C)", outside ? "bad" : "ok"]);

    const eg = c.egress;
    if (eg.requested) {
      let host = eg.destination;
      try { host = new URL(eg.destination).host || eg.destination; } catch (error) { host = eg.destination; }
      setText("egress-dest", host);
      setText("egress-state", eg.allowed ? "destination allowed" : eg.fields.join(", ") + " leaving the plant");
      setState("g-egress", eg.allowed ? "ok" : "bad");
      reads.push(["Recipe egress", eg.recipe_id + " to " + eg.destination, eg.allowed ? "ok" : "bad"]);
    } else {
      setText("egress-dest", (eg.allowed_destinations.join(", ") || "none") + " only");
      setText("egress-state", "no export requested");
      setState("g-egress", null);
    }

    const lc = c.light_curtain;
    if (lc.mute_requested) {
      setText("lc-state", "mute requested");
      setState("g-curtain", "warn");
      reads.push(["Infeed light curtain", lc.mutation_type + " " + lc.requested_value + " for " + lc.duration_sec + " s, key evidence " +
        (lc.key_evidence_presented ? "presented" : "none"), lc.key_evidence_presented ? "warn" : "bad"]);
    } else {
      setText("lc-state", lc.safeguard_ok ? "active" : "fault");
      setState("g-curtain", lc.safeguard_ok ? "ok" : "bad");
      reads.push(["Infeed light curtain", lc.safeguard_ok ? "active, no change requested" : "safeguard fault", lc.safeguard_ok ? "ok" : "bad"]);
    }

    renderSeparation(c, reads);
    reads.push(["Operating mode", c.mode + (c.rush ? ", rush order (journaled, relaxes nothing)" : ""), null]);

    const fam = result ? family(result.outcome) : null;
    $("trays").classList.toggle("running", fam === "RELEASED");
    setText("conveyor-state", fam === "RELEASED" ? "conveyor running: batch released" : result ? "conveyor held: request not released" : "conveyor held (flow left to right)");
    $("lamp-red").classList.toggle("on", fam === "BLOCKED");
    $("lamp-amber").classList.toggle("on", fam === "LOCAL_BLOCK" || fam === "HOLD");
    $("lamp-green").classList.toggle("on", fam === "RELEASED");
    setText("stack-text", fam ? result.outcome : "idle");

    const box = $("readouts");
    box.replaceChildren();
    for (const [label, text, status] of reads) {
      const item = el("div", { cls: status || "" }, el("dt", { text: label }), el("dd", { text }));
      box.append(item);
    }
  }

  function renderSeparation(c, reads) {
    const sep = c.separation;
    const zoneR = Number(sep.collaborative_zone_m) * SCALE;
    const floorR = Number(sep.min_separation_m || 0) * SCALE;
    $("zone-ring").setAttribute("r", String(zoneR));
    $("floor-ring").setAttribute("r", String(floorR));
    $("zone-label").setAttribute("y", String(210 - zoneR + 14));
    $("floor-label").setAttribute("y", String(210 - floorR + 13));
    setText("zone-label", "collaborative zone " + fmt(sep.collaborative_zone_m, 1) + " m");
    setText("floor-label", floorR > 20 ? "floor " + fmt(sep.min_separation_m, 1) + " m" : "");
    const operator = $("g-operator");
    const speed = sep.commanded_speed_mps;
    setText("speed-text", speed !== null && speed !== undefined ? "TCP " + fmt(speed) + " m/s (limit " + fmt(sep.max_tcp_speed_mps) + ")" : "no motion requested");
    if (sep.nearest_human_m === null || sep.nearest_human_m === undefined) {
      operator.classList.add("hidden");
      $("sep-line").classList.add("hidden");
      setState("g-zone", "bad");
      reads.push(["Operator separation", "no reading (treated as missing evidence)", "bad"]);
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
    const status = d < Number(sep.min_separation_m || 0) ? "bad" : d < Number(sep.collaborative_zone_m) ? "warn" : "ok";
    setState("g-zone", status);
    const where = status === "ok" ? "outside the collaborative zone" : status === "warn" ? "inside the collaborative zone" : "below the separation floor";
    reads.push(["Operator separation", fmt(d, 1) + " m, " + where + (speed !== null && speed !== undefined ? ", TCP " + fmt(speed) + " m/s" : ""), status]);
  }

  // -- decision panel --------------------------------------------------------

  function renderDecision() {
    const c = state.cell;
    const row = rowById(c.number);
    const result = row.result || c.result;
    const box = $("outcome");
    setText("row-title", "Row " + c.number + " \u00b7 " + c.area + " \u00b7 " + c.step);
    if (result) {
      box.className = "outcome " + family(result.outcome);
      setText("outcome-text", result.outcome);
      setText("outcome-expected", result.status === "ok" ? "as expected (" + expectedText(row) + ")" :
        result.status === "limit" ? "trial allowance used up, held" : "UNEXPECTED: expected " + expectedText(row));
    } else {
      box.className = "outcome pending";
      setText("outcome-text", row.available ? "not run yet" : "not evaluated, live required");
      setText("outcome-expected", "expected " + expectedText(row));
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
      findings.append(el("li", { cls: "muted", text: result ? (result.stage === "gate" ? "none: every cell check passed, sent for a live verdict" : "none") : "-" }));
    }

    const steering = $("d-steering");
    const directive = result && result.gate && result.gate.steering ? result.gate.steering : result && result.stage === "gate" ? result.reason : null;
    steering.textContent = directive || (result && result.gate ? "none: the verdict allowed the request" : "-");
    steering.classList.toggle("muted", !directive);

    const receipt = $("d-receipt");
    receipt.replaceChildren();
    const add = (label, value, cls) => { receipt.append(el("dt", { text: label }), el("dd", { cls: cls || "", text: value })); };
    if (result && result.gate && result.gate.receipt_id) {
      const g = result.gate;
      add("Receipt id", g.receipt_id, "mono");
      add("Key", g.kid || "-", "mono");
      add("Class", g.receipt_class === "trial" ? "trial (keyless public allowance)" : g.receipt_class || "-");
      add("Verdict", g.verdict || "-");
      add("Signature", g.receipt_verified ? "\u2713 Ed25519 verified before release decision" : "\u2717 did not verify", g.receipt_verified ? "ok-text" : "bad-text");
      add("Host tool ran", result.executed ? "yes" : "no", result.executed ? "ok-text" : "");
    } else if (result) {
      add("Receipt", result.stage === "gate" ? "none returned" : "none: held locally, nothing was sent", "muted");
    } else {
      add("Receipt", "-", "muted");
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
      setText("run-message", res.body.detail || "could not load row " + n);
      return;
    }
    if (state.selected !== n) return;
    state.cell = res.body;
    renderRows();
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
      "Evaluating live on the keyless trial. Requests are spaced at least 2 s apart." : "Evaluating live with RAMEN_API_KEY.") : "Running local cell checks.");
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
        stopAutoplay("All nine requests ran. Export the dossier below.");
        return;
      }
      await selectRow(next.number);
      if (next.live) {
        const pending = state.session.rows.filter((row) => row.live && !row.result).map((row) => row.number);
        if (!next.available) {
          stopAutoplay("Rows " + pending.join(", ") + ": not evaluated, live required. Restart with --trial or --keyed to evaluate them.");
        } else if (state.session.trial_limit_reached) {
          stopAutoplay("The trial allowance is used up for today.");
        } else {
          setText("autoplay-note", "Paused before live row " + next.number + ". Click \u201cEvaluate row " + next.number + " live\u201d to send 1 request.");
          updateControls();
        }
        return;
      }
      await sleep(900);
      if (!state.autoplay) return;
      if (!(await runRow(next.number))) {
        stopAutoplay("Auto-play stopped on an error.");
        return;
      }
      await sleep(1400);
    }
  }

  async function toggleAutoplay() {
    if (state.autoplay) {
      stopAutoplay("Auto-play stopped.");
      return;
    }
    state.autoplay = true;
    setText("autoplay-note", "Auto-play: local rows run in order and pause before every live row.");
    updateControls();
    await continueAutoplay();
  }

  async function step() {
    const next = state.session.rows.find((row) => !row.result);
    if (!next) {
      setText("autoplay-note", "All nine requests ran.");
      return;
    }
    await selectRow(next.number);
    if (next.live) {
      setText("autoplay-note", next.available ? "Row " + next.number + " is live: click \u201cEvaluate row " + next.number + " live\u201d to send 1 request." :
        "Row " + next.number + ": not evaluated, live required. Restart with --trial or --keyed.");
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
    setText("autoplay-note", "New run: fresh issuers, configuration hash, journal and receipts.");
  }

  // -- dossier ---------------------------------------------------------------

  function cell(text, cls) { return el("td", { cls: cls || "", text: text === null || text === undefined ? "-" : text }); }

  async function refreshJournal() {
    const res = await api("GET", "/api/journal");
    if (!res.ok) return;
    const journal = res.body;
    const body = $("journal-body");
    body.replaceChildren();
    for (const entry of journal.entries) {
      const outcome = el("td", null, el("span", { cls: "chip " + family(entry.outcome), text: entry.outcome }));
      const detail = entry.codes.length ? entry.codes.join(", ") : entry.receipt_id ? entry.receipt_id + " (" + entry.receipt_class + ")" : "-";
      const prev = cell(short(entry.prev_sha256, 12), "mono");
      prev.title = entry.prev_sha256;
      const own = cell(short(entry.entry_sha256, 12), "mono");
      own.title = entry.entry_sha256;
      body.append(el("tr", null,
        cell(entry.seq, "mono"),
        cell(entry.timestamp ? entry.timestamp.replace("T", " ").replace("+00:00", "") : "-", "mono"),
        cell(entry.request_kind + (entry.rush ? " (rush)" : "")),
        cell(entry.stage),
        outcome,
        cell(detail, "mono"),
        prev,
        own,
        cell(entry.linked ? "\u2713 linked" : "\u2717 broken", entry.linked ? "ok-text" : "bad-text"),
      ));
    }
    const status = $("chain-status");
    if (!journal.entries.length) {
      status.textContent = "No entries yet. Every decision, hold or release, is journaled here.";
      status.className = "chain-status muted";
    } else if (journal.chain_ok) {
      status.textContent = "Hash chain intact: " + journal.entries.length + " entries from genesis " + short(journal.anchor, 8) +
        " to head " + short(journal.head, 16) + ". Each entry_sha256 covers the canonical entry; each prev_sha256 names the entry before it.";
      status.className = "chain-status ok-text";
    } else {
      status.textContent = "Hash chain broken: " + journal.problems.join("; ");
      status.className = "chain-status bad-text";
    }
  }

  async function refreshReceipts() {
    const res = await api("GET", "/api/receipts");
    if (!res.ok) return;
    const data = res.body;
    const body = $("receipt-body");
    body.replaceChildren();
    for (const item of data.receipts) {
      const text = el("details", null, el("summary", { text: "show" }), el("pre", { text: item.input_text || "" }));
      body.append(el("tr", null,
        cell(item.receipt_id, "mono"),
        cell(item.kid, "mono"),
        cell(item.schema_version ? "V" + String(item.schema_version).split(".")[0] : "-"),
        cell(item.receipt_class),
        cell(item.verdict),
        el("td", null, el("span", { cls: "chip " + family(item.outcome), text: item.outcome })),
        cell(item.verified ? "\u2713 Ed25519 signature and payload hash" : "\u2717 " + (item.problem || "did not verify"), item.verified ? "ok-text" : "bad-text"),
        el("td", null, text),
      ));
    }
    const status = $("receipt-status");
    if (!data.receipts.length) {
      status.textContent = state.session && state.session.mode === "local" ?
        "No live evaluations in local mode. Rows 7 to 9 are not evaluated." : "No live evaluations in this run yet.";
      status.className = "chain-status muted";
    } else {
      const kids = [...new Set(data.receipts.map((item) => item.kid).filter(Boolean))].join(", ");
      status.textContent = data.receipts.length + " Schema V5 receipts (" + data.production + " production, " + data.trial + " trial), " +
        (data.all_verified ? "all re-verified offline under " + kids : "some did not verify");
      status.className = "chain-status " + (data.all_verified ? "ok-text" : "bad-text");
    }
  }

  function renderExport(res) {
    const box = $("export-result");
    const markdown = $("export-md");
    box.replaceChildren();
    if (!res.ok) {
      box.append(el("p", { cls: "bad-text", text: res.body.error === "dossier_refused" ? "Export refused." : res.body.detail || "Export failed." }));
      if (Array.isArray(res.body.problems)) {
        const list = el("ul");
        for (const problem of res.body.problems) list.append(el("li", { text: problem }));
        box.append(list);
      }
      if (res.body.hint) box.append(el("p", { cls: "note", text: res.body.hint }));
      markdown.hidden = true;
      return;
    }
    const d = res.body;
    const facts = el("dl");
    const add = (label, value, cls) => { facts.append(el("dt", { text: label }), el("dd", { cls: cls || "", text: value })); };
    add("verify_dossier", d.intact ? "\u2713 intact: file digests, hash chain, journal-to-receipt join and every receipt re-verified" : "\u2717 " + d.verify.join("; "), d.intact ? "ok-text" : "bad-text");
    add("Directory", d.path, "mono");
    add("Evidence", d.evidence_class ? "evidence_class: " + d.evidence_class : d.evidence_label, d.evidence_class === "trial" ? "warn-text" : "");
    add("Receipts", d.receipts.production + " production, " + d.receipts.trial + " trial");
    add("Decisions", d.decisions + " (" + Object.entries(d.by_outcome).map(([key, value]) => key + " " + value).join(", ") + ")");
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
    $("btn-reset").addEventListener("click", resetRun);
    $("btn-run").addEventListener("click", runSelected);
    $("btn-export").addEventListener("click", exportDossier);
    for (const tab of ["journal", "receipts", "export"]) $("tab-" + tab).addEventListener("click", () => selectTab(tab));
    if (!(await refreshSession())) {
      setText("mode-line", "The monitor is not reachable. Restart workcell_monitor.py and reload.");
      return;
    }
    const notes = {
      local: "Local mode: rows 1 to 6 run on this machine with 0 requests; rows 7 to 9 are not evaluated.",
      trial: "Trial mode: rows 7 to 9 each send one request to the keyless public trial when you click.",
      keyed: "Keyed mode: rows 7 to 9 each send one request with RAMEN_API_KEY and return production receipts.",
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
