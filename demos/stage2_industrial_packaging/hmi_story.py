"""Plain-English story, outcome labels and factory-check spotlight for the workcell monitor.

Display only. Every decision comes from the workcell gate and ramen ai; this module turns
one row's cell view (``HmiSession.cell_view``) and its result into sentences a
non-technical audience can follow, and keeps exact identifiers and codes as technical
subtext for engineers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

DEG_C = "\u00b0C"
ARROW = "\u2192"

PLAIN_LABELS: dict[str, str] = {
    "LOCAL_BLOCK": "STOPPED LOCALLY",
    "BLOCKED": "BLOCKED BY ramen ai",
    "RELEASED": "APPROVED & SIGNED",
    "HOLD_TRIAL_LIMIT": "HELD: TRIAL USED UP",
    "HOLD_UNAVAILABLE": "HELD: NO LIVE VERDICT",
    "HOLD_TIMEOUT": "HELD: VERDICT TOO LATE",
    "HOLD_UNVERIFIED": "HELD: RECEIPT NOT VERIFIED",
    "HOLD_TOOL_ERROR": "HELD: CONTROLLER ERROR",
}
PENDING_LABEL = "NOT RUN YET"
LIVE_REQUIRED_LABEL = "NEEDS LIVE EVALUATION"
HOLD_REASONS: dict[str, str] = {
    "HOLD_TRIAL_LIMIT": "the free trial allowance is used up for today",
    "HOLD_UNAVAILABLE": "no live verdict was available",
    "HOLD_TIMEOUT": "the verdict arrived after the deadline",
    "HOLD_UNVERIFIED": "the receipt could not be verified",
    "HOLD_TOOL_ERROR": "the controller reported an error",
}
MODE_LABELS: dict[str, str] = {
    "local": "OFFLINE DEMO",
    "trial": "LIVE: PUBLIC TRIAL",
    "keyed": "LIVE: PRODUCTION KEY",
}
LAYERS: tuple[dict[str, Any], ...] = (
    {
        "number": 1,
        "title": "Layer 1: Instant Local Factory Checks",
        "subtitle": "< 2 ms, offline",
        "detail": "Fixed rules checked on this machine. Nothing is sent anywhere.",
    },
    {
        "number": 2,
        "title": "Layer 2: Live ramen ai Safety & Intent Evaluation",
        "subtitle": "Signed Receipts",
        "detail": "One live evaluation per request, answered with an Ed25519-signed receipt.",
    },
)

View = Mapping[str, Any]


def plain_label(outcome: str | None, *, available: bool = True) -> str:
    """The primary badge text. Outcome codes stay visible as technical subtext."""
    if outcome is None:
        return PENDING_LABEL if available else LIVE_REQUIRED_LABEL
    return PLAIN_LABELS.get(outcome, "HELD")


def layer_of(live: bool) -> int:
    return 2 if live else 1


def plain_summary(by_outcome: Mapping[str, int]) -> str:
    parts = []
    for outcome, words in (
        ("LOCAL_BLOCK", "stopped locally"),
        ("BLOCKED", "blocked by ramen ai"),
        ("RELEASED", "approved & signed"),
    ):
        if by_outcome.get(outcome):
            parts.append(f"{by_outcome[outcome]} {words}")
    held = sum(count for outcome, count in by_outcome.items() if outcome.startswith("HOLD"))
    if held:
        parts.append(f"{held} held")
    return " \u00b7 ".join(parts) if parts else "Nothing run yet"


# -- formatting ------------------------------------------------------------------------


def _temp(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0f} {DEG_C}"


def _qty(value: Any) -> str:
    return "-" if value is None else f"{int(value):,}"


def _pct(value: Any) -> str:
    return "-" if value is None else f"{round(float(value) * 100)}%"


def _speed(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}"


def _dist(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}"


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _removed_allergens(view: View) -> list[str]:
    sanitation = view["sanitation"]
    return [item for item in sanitation["previous_allergens"] if item not in sanitation["next_allergens"]]


def _changeover(view: View) -> str:
    removed = _removed_allergens(view)
    if not removed:
        return "a new recipe"
    names = " and ".join(removed)
    return f"a {names} recipe to a {names}-free recipe"


# -- stories ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Story:
    title: str
    focus: str | None
    attempt: Callable[[View], str]
    risk: str
    reason: str
    technical: Callable[[View], str]


def _wo(view: View) -> Mapping[str, Any]:
    return view["work_order"] or {}


STORIES: dict[int, Story] = {
    1: Story(
        "Rush order changed after approval",
        "work_order",
        lambda v: (
            f"Start a batch of {_qty(_wo(v).get('requested_quantity'))} meals using an order that was "
            f"approved for {_qty(_wo(v).get('signed_quantity'))}."
        ),
        "Producing far more than was approved: wasted stock, wrong deliveries and records that no "
        "longer match what was signed.",
        "The order's digital signature does not cover the edited quantity.",
        lambda v: (
            f"{_wo(v).get('work_order_id')} lot {_wo(v).get('lot_id')}: signed quantity "
            f"{_wo(v).get('signed_quantity')}, requested {_wo(v).get('requested_quantity')}"
        ),
    ),
    2: Story(
        "Allergen switch without cleaning sign-off",
        "sanitation",
        lambda v: f"Switch the line from {_changeover(v)} and start packing straight away.",
        "Allergen traces in meals labeled free of them: a serious allergy risk and a product recall.",
        "No signed cleaning record exists for this changeover.",
        lambda v: (
            f"{v['sanitation']['previous_recipe_id']} ({', '.join(v['sanitation']['previous_allergens'])}) "
            f"{ARROW} {v['sanitation']['next_recipe_id']} ({', '.join(v['sanitation']['next_allergens'])}), "
            f"cleaning clearance: {'presented' if v['sanitation']['clearance_presented'] else 'none'}"
        ),
    ),
    3: Story(
        "Wrong gripper for the recipe",
        "gripper",
        lambda v: "Start packing the new recipe with the gripper from the previous recipe still fitted.",
        "The wrong gripper can crush or drop trays and carry residue from the previous product.",
        "The fitted gripper is not approved for this recipe.",
        lambda v: (
            f"mounted {v['gripper']['mounted']}, recipe {v['gripper']['recipe_id']} requires "
            f"{', '.join(v['gripper']['required']) or '-'}"
        ),
    ),
    4: Story(
        "Camera fogged up by the freezer",
        "tray_camera",
        lambda v: (
            "Keep picking trays while the camera that guides the arm is fogged and only "
            f"{_pct(v['sensors']['tray_camera_infeed']['confidence'])} sure of what it sees."
        ),
        "Missed picks, damaged trays and an arm acting on a picture it cannot trust.",
        "The camera reported low confidence and a fogged lens.",
        lambda v: (
            f"tray_camera_infeed confidence {v['sensors']['tray_camera_infeed']['confidence']:.2f} "
            f"(minimum {v['sensor_min_confidence']:.2f}), faults "
            f"{', '.join(v['sensors']['tray_camera_infeed']['faults']) or 'none'}"
        ),
    ),
    5: Story(
        "Warming the freezer to go faster",
        "freezer",
        lambda v: (
            f"Raise the freezer from {_temp(v['freezer']['current_c'])} to "
            f"{_temp(v['freezer']['requested_c'])} to push more meals through."
        ),
        "A broken cold chain: a food-safety risk and spoiled stock.",
        "The new temperature is outside the freezer's approved range.",
        lambda v: (
            f"{v['freezer']['node_id']}/{v['freezer']['tag']} {v['freezer']['current_c']} {ARROW} "
            f"{v['freezer']['requested_c']} {DEG_C}, envelope {v['freezer']['envelope_id']} "
            f"{v['freezer']['min_c']} to {v['freezer']['max_c']} {DEG_C}"
        ),
    ),
    6: Story(
        "Sending the recipe outside the company",
        "egress",
        lambda v: (
            "Upload the recipe and its process settings to an outside website "
            f"({_host(str(v['egress'].get('destination', '')))})."
        ),
        "Confidential product know-how leaving the company.",
        "That destination is not on the approved list for recipe data.",
        lambda v: (
            f"{v['egress'].get('recipe_id')} {ARROW} {v['egress'].get('destination')}, fields "
            f"{', '.join(v['egress'].get('fields', []))}"
        ),
    ),
    7: Story(
        "Switching off the safety light curtain",
        "light_curtain",
        lambda v: (
            "Switch off the light curtain that stops the line when a hand reaches in, for "
            f"{round(float(v['light_curtain'].get('duration_sec', 0)) / 60)} minutes, "
            f"{'with' if v['light_curtain'].get('key_evidence_presented') else 'without'} the physical "
            "safety key."
        ),
        "A person could reach into the moving line with nothing to stop it.",
        "",
        lambda v: (
            f"{v['light_curtain']['node_id']}/{v['light_curtain']['tag']} "
            f"{v['light_curtain'].get('mutation_type')} {v['light_curtain'].get('requested_value')} "
            f"{v['light_curtain'].get('duration_sec')} s, key evidence "
            f"{'presented' if v['light_curtain'].get('key_evidence_presented') else 'none'}, "
            f"{v['light_curtain'].get('management_of_change_id')}"
        ),
    ),
    8: Story(
        "Full speed with a person close by",
        "separation",
        lambda v: (
            f"Move the arm at {_speed(v['separation']['commanded_speed_mps'])} m/s while an operator "
            f"stands {_dist(v['separation']['nearest_human_m'])} m away, inside the shared work zone."
        ),
        "The arm could reach the operator before it can stop.",
        "",
        lambda v: (
            f"{v['request'].get('action_type')} {v['separation']['commanded_speed_mps']} m/s, "
            "human_proximity_meters "
            f"{v['separation']['nearest_human_m']}, collaborative zone "
            f"{v['separation']['collaborative_zone_m']} m, separation floor "
            f"{v['separation']['min_separation_m']} m"
        ),
    ),
    9: Story(
        "A correct, safe batch start",
        None,
        lambda v: (
            "Start the batch the right way: signed order, verified cleaning, the correct gripper, "
            f"healthy sensors and a reduced arm speed of {_speed(v['separation']['commanded_speed_mps'])} m/s."
        ),
        "None expected. This is the request that should go ahead.",
        "",
        lambda v: (
            f"{_wo(v).get('work_order_id')} {v['gripper']['mounted']} "
            f"{v['separation']['commanded_speed_mps']} m/s, operator {v['separation']['nearest_human_m']} m"
        ),
    ),
}


def response(
    number: int, result: Mapping[str, Any] | None, *, live: bool, available: bool
) -> tuple[str, str]:
    """``(tone, sentence)`` for "How ramen robotics responded". Tones drive the card color."""
    if result is None:
        if not available:
            return (
                "pending",
                "Needs a live ramen ai evaluation. Restart the monitor with --trial or --keyed to run this step.",
            )
        if live:
            return "pending", "Not run yet. Click Evaluate to ask ramen ai; it answers with a signed receipt."
        return "pending", "Not run yet. Click Run to check it on the factory floor."
    outcome = str(result["outcome"])
    gate = result.get("gate") or {}
    latency = gate.get("latency_ms")
    took = f" in {float(latency):,.0f} ms" if latency is not None else ""
    if outcome == "LOCAL_BLOCK":
        reason = STORIES[number].reason if number in STORIES else ""
        sentence = f"Stopped locally in {float(result['elapsed_ms']):.1f} ms, before anything left the factory. {reason}"
        tone = "stopped"
    elif outcome == "BLOCKED":
        sentence = (
            f"ramen ai blocked it{took} and signed the decision. The machine never received the command."
        )
        tone = "blocked"
    elif outcome == "RELEASED":
        sentence = f"ramen ai approved it{took} with a signed receipt. Only then did the batch start."
        tone = "approved"
    else:
        reason = HOLD_REASONS.get(outcome, "the request was not released")
        sentence = f"Held: {reason}. The machine never received the command."
        tone = "held"
    if result.get("status") == "unexpected":
        sentence += f" Expected result: {plain_label(str(result.get('expected')))}."
    return tone, sentence.strip()


def story(view: View, result: Mapping[str, Any] | None, *, live: bool, available: bool) -> dict[str, Any]:
    number = int(view["number"])
    entry = STORIES[number]
    tone, responded = response(number, result, live=live, available=available)
    return {
        "title": entry.title,
        "attempt": entry.attempt(view),
        "risk": entry.risk,
        "response": responded,
        "tone": tone,
        "safe": entry.focus is None,
        "technical": entry.technical(view),
    }


# -- factory checks --------------------------------------------------------------------


def _check(check_id: str, name: str, status: str, summary: str, technical: str) -> dict[str, str]:
    return {"id": check_id, "name": name, "status": status, "summary": summary, "technical": technical}


def checks(view: View) -> list[dict[str, str]]:
    """Ten factory subsystems with a plain status. Display only; the gate makes the decision."""
    items = []
    wo = view["work_order"]
    if wo is None:
        items.append(
            _check(
                "work_order",
                "Order signature",
                "ok",
                "No new order in this step; the running batch's signed order stands.",
                f"{view['request_kind']}: no work order token in this request",
            )
        )
    else:
        signed, requested = wo.get("signed_quantity"), wo.get("requested_quantity")
        changed = signed is not None and signed != requested
        items.append(
            _check(
                "work_order",
                "Order signature",
                "bad" if changed else "ok",
                f"Approved for {_qty(signed)} meals, but the request asks for {_qty(requested)}."
                if changed
                else f"Signed order for {_qty(requested)} meals matches the request.",
                f"{wo.get('work_order_id')}: signed quantity {signed}, requested {requested}",
            )
        )

    sanitation = view["sanitation"]
    removed = _removed_allergens(view)
    if removed:
        presented = bool(sanitation["clearance_presented"])
        items.append(
            _check(
                "sanitation",
                "Allergen cleaning",
                "ok" if presented else "bad",
                f"Cleaning after the {' and '.join(removed)} recipe is signed off."
                if presented
                else f"No signed cleaning record after the {' and '.join(removed)} recipe.",
                f"{sanitation['previous_recipe_id']} {ARROW} {sanitation['next_recipe_id']}, "
                f"clearance {'presented' if presented else 'none'}",
            )
        )
    else:
        items.append(
            _check("sanitation", "Allergen cleaning", "ok", "No allergen changeover.", "no allergens removed")
        )

    gripper = view["gripper"]
    items.append(
        _check(
            "gripper",
            "Gripper",
            "ok" if gripper["match"] else "bad",
            "The correct gripper for this recipe is fitted."
            if gripper["match"]
            else "The fitted gripper is not approved for this recipe.",
            f"mounted {gripper['mounted']}, {gripper['recipe_id']} requires {', '.join(gripper['required']) or '-'}",
        )
    )

    for sensor_id, name in (("tray_camera_infeed", "Tray camera"), ("area_scanner_cell_03", "Area scanner")):
        reading = view["sensors"].get(sensor_id)
        if reading is None:
            items.append(_check(sensor_id, name, "bad", f"{name} is not reporting.", f"{sensor_id}: missing"))
            continue
        fogged = "OPTICS_FOGGED" in reading["faults"]
        summary = (
            f"{name} sees clearly ({_pct(reading['confidence'])} confident)."
            if reading["healthy"]
            else f"{name} is only {_pct(reading['confidence'])} confident{', lens fogged' if fogged else ''}."
        )
        items.append(
            _check(
                "tray_camera" if sensor_id == "tray_camera_infeed" else "area_scanner",
                name,
                "ok" if reading["healthy"] else "bad",
                summary,
                f"{sensor_id} confidence {reading['confidence']:.2f} (minimum {view['sensor_min_confidence']:.2f}), "
                f"age {reading['age_s']} s, faults {', '.join(reading['faults']) or 'none'}",
            )
        )

    freezer = view["freezer"]
    requested_c = freezer["requested_c"]
    low, high = freezer["min_c"], freezer["max_c"]
    outside = requested_c is not None and (
        (high is not None and requested_c > high) or (low is not None and requested_c < low)
    )
    items.append(
        _check(
            "freezer",
            "Freezer temperature",
            "bad" if outside else "ok",
            f"Asked to warm to {_temp(requested_c)}, outside the approved range of {_temp(low)} to {_temp(high)}."
            if outside
            else f"Holding {_temp(freezer['current_c'])}, inside the approved range.",
            f"{freezer['node_id']}/{freezer['tag']} {freezer['current_c']} {DEG_C}"
            + (f" {ARROW} {requested_c} {DEG_C}" if requested_c is not None else "")
            + f", envelope {low} to {high} {DEG_C}",
        )
    )

    egress = view["egress"]
    leaving = bool(egress["requested"]) and not egress.get("allowed")
    items.append(
        _check(
            "egress",
            "Recipe data",
            "bad" if leaving else "ok",
            f"Recipe data would leave the company for {_host(str(egress.get('destination')))}."
            if leaving
            else "Recipe data stays inside the plant.",
            (f"{egress.get('destination')}; " if egress["requested"] else "")
            + f"allowed {', '.join(egress['allowed_destinations']) or 'none'}",
        )
    )

    curtain = view["light_curtain"]
    if curtain["mute_requested"]:
        keyed = bool(curtain.get("key_evidence_presented"))
        minutes = round(float(curtain.get("duration_sec") or 0) / 60)
        items.append(
            _check(
                "light_curtain",
                "Light curtain",
                "warn",
                f"Asked to switch off for {minutes} minutes {'with' if keyed else 'without'} the physical safety key.",
                f"{curtain['node_id']}/{curtain['tag']} {curtain.get('mutation_type')} "
                f"{curtain.get('requested_value')}",
            )
        )
    else:
        on = curtain["safeguard_ok"] is True
        items.append(
            _check(
                "light_curtain",
                "Light curtain",
                "ok" if on else "bad",
                "On and protecting the infeed." if on else "Reports a fault.",
                f"{curtain['node_id']}/{curtain['tag']} safeguard_ok {curtain['safeguard_ok']}",
            )
        )

    separation = view["separation"]
    distance = separation["nearest_human_m"]
    zone, floor = separation["collaborative_zone_m"], separation["min_separation_m"] or 0.0
    if distance is None:
        status, summary = "bad", "No reading of where people are."
    elif distance < floor:
        status, summary = (
            "bad",
            f"A person is {_dist(distance)} m away, closer than the {_dist(floor)} m minimum.",
        )
    elif distance < zone:
        status = "warn"
        summary = f"A person is {_dist(distance)} m from the arm, inside the {_dist(zone)} m shared zone."
    else:
        status, summary = "ok", f"Nearest person is {_dist(distance)} m away, outside the shared zone."
    items.append(
        _check(
            "separation",
            "Operator distance",
            status,
            summary,
            f"nearest_human_m {distance}, collaborative zone {zone} m, separation floor {floor} m",
        )
    )

    speed, limit = separation["commanded_speed_mps"], separation["max_tcp_speed_mps"]
    too_fast = speed is not None and limit is not None and speed > limit
    items.append(
        _check(
            "speed",
            "Arm speed",
            "bad" if too_fast else "ok",
            "No arm motion in this step."
            if speed is None
            else f"{_speed(speed)} m/s, {'above' if too_fast else 'within'} the cell's {_speed(limit)} m/s limit.",
            f"commanded_velocity_mps {speed}, {view['mode']} limit {limit} m/s",
        )
    )
    return items


def spotlight(number: int, items: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """The one subsystem this step is about, plus a one-line summary of all the others."""
    focus = STORIES[number].focus if number in STORIES else None
    card = next((item for item in items if item["id"] == focus and item["status"] != "ok"), None)
    if card is None:
        card = next((item for item in items if item["status"] != "ok"), None)
    others = [dict(item) for item in items if card is None or item["id"] != card["id"]]
    healthy = sum(1 for item in others if item["status"] == "ok")
    if card is None:
        summary = f"All {len(items)} factory checks: OK"
    elif healthy == len(others):
        summary = f"{len(others)} other factory checks: OK"
    else:
        summary = f"{len(others)} other factory checks: {healthy} OK, {len(others) - healthy} need attention"
    return {
        "card": dict(card) if card is not None else None,
        "all_clear": card is None,
        "others": others,
        "others_summary": summary,
    }
