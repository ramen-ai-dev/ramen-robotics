"""Stage 2 workcell monitor, offline: session, router, handler adapter, bind address and assets.

The autouse socket guard in ``conftest.py`` refuses ``connect``, so no test opens a client
connection. Routes are exercised through ``HmiApp.handle``, the real request handler runs
against an in-memory connection, and the bind test only binds and closes a listener.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from demos.stage2_industrial_packaging import hmi_http, hmi_session, workcell_monitor
from demos.stage2_industrial_packaging.hmi_session import HmiSession, SessionError

from tests.fakes import FAKE_API_KEY, FakeRamenServer
from tests.test_demos import load
from tests.test_workcell_gate import workcell_decide

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demos" / "stage2_industrial_packaging"
NO_SPACING = {"min_interval_s": 0.0}
TOKEN = "test-token-0123456789"
PORT = 8765
HOST = {"Host": f"127.0.0.1:{PORT}"}
POST = {**HOST, "X-HMI-Token": TOKEN}
NEW_FILES = (
    DEMO / "workcell_monitor.py",
    DEMO / "hmi_session.py",
    DEMO / "hmi_http.py",
    DEMO / "hmi" / "index.html",
    DEMO / "hmi" / "hmi.css",
    DEMO / "hmi" / "hmi.js",
    DEMO / "README.md",
)
ALL_INTERFACES = ".".join("0" * 4)
ASSETS = (DEMO / "hmi" / "index.html", DEMO / "hmi" / "hmi.css", DEMO / "hmi" / "hmi.js")


def local_session(tmp_path: Path, **kwargs: Any) -> HmiSession:
    return HmiSession("local", environ={}, run_root=tmp_path / "runs", **kwargs)


def trial_session(tmp_path: Path, server: FakeRamenServer, **kwargs: Any) -> HmiSession:
    return HmiSession(
        "trial",
        environ={},
        run_root=tmp_path / "runs",
        client=server.trial_client(),
        gate_options=NO_SPACING,
        **kwargs,
    )


def keyed_session(tmp_path: Path, server: FakeRamenServer) -> HmiSession:
    return HmiSession(
        "keyed",
        environ={"RAMEN_API_KEY": FAKE_API_KEY},
        run_root=tmp_path / "runs",
        client=server.keyed_client(),
        gate_options=NO_SPACING,
    )


def run_all(session: HmiSession) -> dict[int, dict[str, Any]]:
    return {number: session.run_row(number) for number in range(1, 10)}


def app_for(session: HmiSession) -> hmi_http.HmiApp:
    return hmi_http.HmiApp(session, token=TOKEN, port=PORT)


# -- session ----------------------------------------------------------------------


def test_local_session_holds_rows_1_to_6_and_sends_nothing(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    expected = {
        1: "WORK_ORDER_MISMATCH",
        2: "SANITATION_UNVERIFIED",
        3: "TOOLING_MISMATCH",
        4: "SENSOR_DEGRADED",
        5: "PLC_OUT_OF_ENVELOPE",
        6: "EGRESS_DENIED",
    }
    for number, code in expected.items():
        result = session.run_row(number)
        assert result["outcome"] == "LOCAL_BLOCK" and result["status"] == "ok"
        assert code in {finding["code"] for finding in result["findings"]}
        assert result["gate"] is None and result["stage"] == "precheck"
    for number in (7, 8, 9):
        with pytest.raises(SessionError) as refused:
            session.run_row(number)
        assert refused.value.code == "live_disabled"
    view = session.session_view()
    live_rows = [row for row in view["rows"] if row["live"]]
    assert [row["number"] for row in live_rows] == [7, 8, 9]
    assert all(not row["available"] and row["note"] == hmi_session.NOT_EVALUATED for row in live_rows)
    assert all(row["result"] is None for row in live_rows)
    assert view["mode"] == "local" and view["endpoint"] is None and view["quota"] == {}
    assert view["summary"]["live_requests"] == 0 and session.reached == []
    assert "--keyed" in view["mode_line"]
    journal = session.journal_view()
    assert journal["chain_ok"] and len(journal["entries"]) == 6
    assert all(entry["linked"] for entry in journal["entries"])
    assert session.receipts_view()["receipts"] == []


def test_trial_session_runs_rows_7_to_9_with_three_requests(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    session = trial_session(tmp_path, server)
    results = run_all(session)
    assert all(result["status"] == "ok" for result in results.values())
    assert [results[n]["outcome"] for n in (7, 8, 9)] == ["BLOCKED", "BLOCKED", "RELEASED"]
    assert len(server.requests) == 3
    assert all(r.url.path == "/api/v1/paas/try" and "authorization" not in r.headers for r in server.requests)
    for number in (7, 8, 9):
        gate = results[number]["gate"]
        assert gate["kid"] == "ramen_pk_v1" and gate["receipt_class"] == "trial" and gate["receipt_verified"]
        assert gate["receipt_id"].startswith("trial_")
    assert results[7]["gate"]["steering"] and results[9]["executed"] is True
    assert len(session.reached) == 1
    view = session.session_view()
    assert view["endpoint"] == "https://api.ramenai.dev/api/v1/paas/try"
    assert view["quota"]["limit"] == 10 and "remaining" in view["quota"]
    assert view["summary"] == {
        "ran": 9,
        "ok": 9,
        "unexpected": 0,
        "live_requests": 3,
        "by_outcome": {"LOCAL_BLOCK": 6, "BLOCKED": 2, "RELEASED": 1},
    }
    receipts = session.receipts_view()
    assert receipts["trial"] == 3 and receipts["production"] == 0 and receipts["all_verified"]
    assert all(item["schema_version"] == "5.0" and item["input_text"] for item in receipts["receipts"])
    assert session.journal_view()["chain_ok"]


def test_a_row_runs_once_per_run(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    session = trial_session(tmp_path, server)
    session.run_row(9)
    with pytest.raises(SessionError) as refused:
        session.run_row(9)
    assert refused.value.code == "already_run" and len(server.requests) == 1


def test_trial_limit_stops_further_live_rows(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide, limit_after=1)
    session = trial_session(tmp_path, server)
    assert session.run_row(7)["outcome"] == "BLOCKED"
    held = session.run_row(8)
    assert held["outcome"] == "HOLD_TRIAL_LIMIT" and held["status"] == "limit"
    with pytest.raises(SessionError) as refused:
        session.run_row(9)
    assert refused.value.code == "trial_limit"
    assert len(server.requests) == 2 and session.session_view()["trial_limit_reached"]


def test_busy_session_refuses_instead_of_queueing(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    with session._lock:
        for call in (lambda: session.run_row(1), session.export, session.reset):
            with pytest.raises(SessionError) as refused:
                call()
            assert refused.value.code == "busy"
        assert session.session_view()["busy"] is True


def test_unknown_rows_are_not_found(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    for number in (0, 10):
        with pytest.raises(SessionError) as missing:
            session.cell_view(number)
        assert missing.value.status == 404


def test_cell_view_feeds_the_schematic(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    order = session.cell_view(1)["work_order"]
    assert order["signed_quantity"] == 1200 and order["requested_quantity"] == 4800
    assert session.cell_view(2)["sanitation"]["clearance_presented"] is False
    assert session.cell_view(9)["sanitation"]["clearance_presented"] is True
    gripper = session.cell_view(3)["gripper"]
    assert (
        gripper["mounted"] == "GRP-VAC-02" and gripper["required"] == ["GRP-SOFT-04"] and not gripper["match"]
    )
    camera = session.cell_view(4)["sensors"]["tray_camera_infeed"]
    assert camera["confidence"] == 0.41 and camera["faults"] == ["OPTICS_FOGGED"] and not camera["healthy"]
    freezer = session.cell_view(5)["freezer"]
    assert freezer["current_c"] == -18.0 and freezer["requested_c"] == -12.0 and freezer["max_c"] == -18.0
    egress = session.cell_view(6)["egress"]
    assert egress["requested"] and not egress["allowed"]
    assert session.cell_view(1)["egress"] == {
        "requested": False,
        "allowed_destinations": ["mes://plant-07/recipes"],
    }
    curtain = session.cell_view(7)["light_curtain"]
    assert curtain["mute_requested"] and curtain["key_evidence_presented"] is False
    separation = session.cell_view(8)["separation"]
    assert separation["nearest_human_m"] == 0.6 and separation["commanded_speed_mps"] == 1.2
    assert separation["collaborative_zone_m"] == 1.0 and separation["min_separation_m"] == 0.5
    text = json.dumps([session.cell_view(n) for n in range(1, 10)])
    assert '"work_order_token_sha256"' in text and "v1.mes-1." not in text and "v1.sanitation-1." not in text


def test_token_claims_is_display_only_and_tolerant() -> None:
    assert hmi_session.token_claims(None) == {}
    assert hmi_session.token_claims("not-a-token") == {}
    assert hmi_session.token_claims("v1.kid.!!!.sig") == {}


def test_local_dossier_reports_receipt_counts(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    with pytest.raises(SessionError) as empty:
        session.export()
    assert empty.value.code == "no_decisions"
    for number in range(1, 7):
        session.run_row(number)
    dossier = session.export()
    assert dossier["intact"] and dossier["verify"] == []
    assert dossier["evidence_class"] is None and dossier["evidence_label"] == "local checks only (0 receipts)"
    assert dossier["receipts"] == {"production": 0, "trial": 0} and dossier["decisions"] == 6
    assert "CELL-PACK-03" in dossier["markdown"]
    assert session.dossier_view() == dossier
    assert Path(dossier["path"]).name == "dossier-01"
    assert Path(session.export()["path"]).name == "dossier-02"


def test_trial_dossier_is_refused_without_the_flag(tmp_path: Path) -> None:
    session = trial_session(tmp_path, FakeRamenServer(decide=workcell_decide))
    run_all(session)
    with pytest.raises(SessionError) as refused:
        session.export()
    assert refused.value.code == "dossier_refused"
    assert refused.value.extra["trial_refused"] is True
    assert "--allow-trial-dossier" in refused.value.extra["hint"]
    assert session.dossier_view() == {"exported": False}


def test_trial_dossier_with_the_flag_is_labeled_trial(tmp_path: Path) -> None:
    session = trial_session(tmp_path, FakeRamenServer(decide=workcell_decide), allow_trial_dossier=True)
    run_all(session)
    dossier = session.export()
    assert dossier["intact"] and dossier["evidence_class"] == "trial" and dossier["evidence_label"] == "trial"
    assert dossier["receipts"] == {"production": 0, "trial": 3}


def test_keyed_session_exports_a_production_dossier(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    session = keyed_session(tmp_path, server)
    results = run_all(session)
    assert all(result["status"] == "ok" for result in results.values())
    assert {r.url.path for r in server.requests} == {"/api/v1/paas/evaluate"}
    assert results[9]["gate"]["receipt_class"] == "production"
    dossier = session.export()
    assert dossier["intact"] and dossier["evidence_class"] == "production"
    assert dossier["receipts"] == {"production": 3, "trial": 0}
    app = app_for(session)
    for path in ("/api/session", "/api/journal", "/api/receipts", "/api/dossier", "/", "/hmi.js"):
        assert FAKE_API_KEY.encode() not in app.handle("GET", path, HOST).body
    assert session.session_view()["endpoint"] == "https://api.ramenai.dev/api/v1/paas/evaluate"


def test_reset_starts_a_new_run(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    session.run_row(1)
    before = session.session_view()
    after = session.reset()
    assert after["run_dir"] != before["run_dir"] and after["summary"]["ran"] == 0
    assert after["cell"]["config_hash"] != before["cell"]["config_hash"]
    assert session.journal_view()["entries"] == []
    assert Path(before["journal_path"]).is_file()


def test_session_rejects_a_mode_the_environment_does_not_resolve_to(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        HmiSession("keyed", environ={}, run_root=tmp_path)
    with pytest.raises(ValueError):
        HmiSession("local", environ={"RAMEN_API_KEY": FAKE_API_KEY}, run_root=tmp_path)


# -- router -----------------------------------------------------------------------


def test_index_carries_the_token_and_a_strict_policy(tmp_path: Path) -> None:
    response = app_for(local_session(tmp_path)).handle("GET", "/", HOST)
    assert response.status == 200 and response.content_type.startswith("text/html")
    assert TOKEN.encode() in response.body and hmi_http.TOKEN_PLACEHOLDER.encode() not in response.body
    headers = dict(response.headers)
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Cache-Control"] == "no-store"


def test_static_files_are_an_allowlist(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    assert app.handle("GET", "/hmi.css", HOST).content_type.startswith("text/css")
    assert app.handle("GET", "/hmi.js?v=1", HOST).content_type.startswith("text/javascript")
    for path in (
        "/../pyproject.toml",
        "/hmi/../scenario.py",
        "/%2e%2e/scenario.py",
        "/hmi_http.py",
        "/favicon.ico",
    ):
        assert app.handle("GET", path, HOST).status == 404, path


@pytest.mark.parametrize(
    "host", [None, "evil.example:8765", "127.0.0.1:9999", "localhost", ".".join("0" * 4) + ":8765"]
)
def test_host_header_must_name_the_bound_port(tmp_path: Path, host: str | None) -> None:
    headers = {} if host is None else {"Host": host}
    response = app_for(local_session(tmp_path)).handle("GET", "/api/session", headers)
    assert response.status == 403 and response.json()["error"] == "host_not_allowed"


def test_localhost_name_is_accepted(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    assert app.handle("GET", "/api/session", {"Host": f"localhost:{PORT}"}).status == 200


def test_posts_need_the_token_and_a_matching_origin(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    app = app_for(session)
    assert app.handle("POST", "/api/rows/1/run", HOST).status == 403
    assert app.handle("POST", "/api/rows/1/run", {**HOST, "X-HMI-Token": "wrong"}).status == 403
    foreign = app.handle("POST", "/api/rows/1/run", {**POST, "Origin": "https://evil.example"})
    assert foreign.status == 403 and foreign.json()["error"] == "origin_not_allowed"
    assert session.results == {}
    allowed = app.handle("POST", "/api/rows/1/run", {**POST, "Origin": f"http://127.0.0.1:{PORT}"})
    assert allowed.status == 200 and allowed.json()["outcome"] == "LOCAL_BLOCK"


def test_route_errors(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    assert app.handle("GET", "/api/nope", HOST).status == 404
    assert app.handle("GET", "/api/rows/0/cell", HOST).status == 404
    assert app.handle("POST", "/api/rows/10/run", POST).status == 404
    not_allowed = app.handle("GET", "/api/rows/1/run", HOST)
    assert not_allowed.status == 405 and ("Allow", "POST") in not_allowed.headers
    assert app.handle("POST", "/api/session", POST).status == 405
    assert app.handle("POST", "/", POST).status == 405
    assert app.handle("GET", "/api/reset", HOST).status == 405
    live = app.handle("POST", "/api/rows/7/run", POST)
    assert live.status == 409 and live.json()["error"] == "live_disabled"
    assert app.handle("POST", "/api/rows/1/run", POST).status == 200
    assert app.handle("POST", "/api/rows/1/run", POST).json()["error"] == "already_run"


def test_routes_return_json_views(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    assert app.handle("GET", "/api/rows/8/cell", HOST).json()["separation"]["nearest_human_m"] == 0.6
    app.handle("POST", "/api/rows/1/run", POST)
    assert len(app.handle("GET", "/api/journal", HOST).json()["entries"]) == 1
    assert app.handle("GET", "/api/receipts", HOST).json()["receipts"] == []
    assert app.handle("GET", "/api/dossier", HOST).json() == {"exported": False}
    exported = app.handle("POST", "/api/dossier/export", POST).json()
    assert exported["intact"] and app.handle("GET", "/api/dossier", HOST).json()["exported"]
    assert app.handle("POST", "/api/reset", POST).json()["summary"]["ran"] == 0


def test_refused_trial_dossier_carries_the_hint(tmp_path: Path) -> None:
    session = trial_session(tmp_path, FakeRamenServer(decide=workcell_decide))
    session.run_row(9)
    response = app_for(session).handle("POST", "/api/dossier/export", POST)
    body = response.json()
    assert response.status == 409 and body["error"] == "dossier_refused" and body["trial_refused"]
    assert "--allow-trial-dossier" in body["hint"]


def test_trial_quota_is_passed_through_generically(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    client = server.trial_client()
    session = HmiSession(
        "trial", environ={}, run_root=tmp_path / "runs", client=client, gate_options=NO_SPACING
    )
    client.last_quota = {"limit": 10, "remaining": 4, "reset_epoch_seconds": 1893456000, "scope": "network"}
    quota = app_for(session).handle("GET", "/api/session", HOST).json()["quota"]
    assert quota == {"limit": 10, "remaining": 4, "reset_epoch_seconds": 1893456000, "scope": "network"}


def test_unexpected_errors_reveal_only_the_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session = local_session(tmp_path)

    def broken() -> dict[str, Any]:
        raise RuntimeError("internal detail " + FAKE_API_KEY)

    session.journal_view = broken  # type: ignore[method-assign]
    response = app_for(session).handle("GET", "/api/journal", HOST)
    assert response.status == 500 and response.json() == {"error": "internal_error", "detail": "RuntimeError"}
    assert FAKE_API_KEY not in capsys.readouterr().err


# -- handler adapter, in memory ---------------------------------------------------


class _Connection:
    """Just enough of a socket for ``StreamRequestHandler``: reads from bytes, collects writes."""

    def __init__(self, data: bytes) -> None:
        self._reader = io.BytesIO(data)
        self.written = io.BytesIO()

    def makefile(self, mode: str, *args: Any, **kwargs: Any) -> io.BytesIO:
        return self._reader if "r" in mode else self.written

    def sendall(self, data: bytes) -> None:
        self.written.write(data)


def exchange(app: hmi_http.HmiApp, raw: bytes) -> tuple[int, dict[str, str], bytes]:
    connection = _Connection(raw)
    handler_class = hmi_http.make_handler(app)
    handler_class(connection, ("127.0.0.1", 50000), object())  # type: ignore[arg-type]
    head, _, body = connection.written.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


def request(method: str, path: str, headers: dict[str, str], body: bytes = b"") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", *(f"{k}: {v}" for k, v in headers.items()), "Connection: close"]
    if (body or method == "POST") and "Content-Length" not in headers:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def test_handler_serves_json_with_security_headers(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    status, headers, body = exchange(app, request("GET", "/api/session", HOST))
    assert status == 200 and json.loads(body)["mode"] == "local"
    assert headers["content-type"].startswith("application/json")
    assert int(headers["content-length"]) == len(body)
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["server"] == "workcell-monitor"


def test_handler_runs_a_post(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    status, _, body = exchange(app, request("POST", "/api/rows/3/run", POST))
    assert status == 200 and json.loads(body)["findings"][0]["code"] == "TOOLING_MISMATCH"


def test_handler_bounds_the_body(tmp_path: Path) -> None:
    session = local_session(tmp_path)
    app = app_for(session)
    raw = request("POST", "/api/rows/1/run", {**POST, "Content-Length": str(hmi_http.MAX_BODY_BYTES + 1)})
    status, _, body = exchange(app, raw)
    assert status == 413 and json.loads(body)["error"] == "body_too_large" and session.results == {}
    status, _, _ = exchange(app, request("POST", "/api/rows/1/run", {**POST, "Content-Length": "x"}))
    assert status == 400


def test_handler_rejects_duplicate_host_headers(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    raw = (
        f"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nHost: evil.example\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    assert exchange(app, raw)[0] == 400


def test_handler_leaves_other_methods_unimplemented(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    for method in ("PUT", "DELETE", "OPTIONS"):
        assert exchange(app, request(method, "/api/reset", POST))[0] == 501


# -- bind address and CLI ---------------------------------------------------------


def test_server_binds_to_loopback_only(tmp_path: Path) -> None:
    app = app_for(local_session(tmp_path))
    server = hmi_http.make_server(app, 0)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1" and port != 0 and app.port == port
        assert f"127.0.0.1:{port}" in app.allowed_hosts
    finally:
        server.server_close()
    assert hmi_http.BIND_HOST == "127.0.0.1"


def test_cli_has_no_host_option() -> None:
    with pytest.raises(SystemExit):
        workcell_monitor.parse_arguments(["--host", ALL_INTERFACES])


def test_cli_modes_are_mutually_exclusive() -> None:
    assert workcell_monitor.parse_arguments([]).mode == "local"
    assert workcell_monitor.parse_arguments(["--trial"]).mode == "trial"
    assert workcell_monitor.parse_arguments(["--keyed"]).mode == "keyed"
    assert workcell_monitor.parse_arguments([]).port == 8765
    for argv in (["--local", "--trial"], ["--trial", "--keyed"], ["--port", "-1"], ["--port", "65536"]):
        with pytest.raises(SystemExit):
            workcell_monitor.parse_arguments(argv)


def test_mode_environment_strips_the_key_outside_keyed_mode() -> None:
    env = {"RAMEN_API_KEY": FAKE_API_KEY, "RAMEN_BASE_URL": "https://eu.ramenai.dev"}
    assert workcell_monitor.mode_environment("local", env) == {"RAMEN_BASE_URL": "https://eu.ramenai.dev"}
    assert "RAMEN_API_KEY" not in workcell_monitor.mode_environment("trial", env)
    assert workcell_monitor.mode_environment("keyed", env)["RAMEN_API_KEY"] == FAKE_API_KEY
    with pytest.raises(workcell_monitor.ModeError):
        workcell_monitor.mode_environment("keyed", {"RAMEN_API_KEY": "  "})


def test_keyed_without_a_key_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = workcell_monitor.main(["--keyed", "--run-dir", str(tmp_path)], environ={})
    assert code == 2 and "RAMEN_API_KEY" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def serve_once(captured: list[Any]) -> Any:
    def serve(server: Any) -> None:
        captured.append(server)

    return serve


def test_main_local_ignores_a_set_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured: list[Any] = []
    code = workcell_monitor.main(
        ["--port", "0", "--run-dir", str(tmp_path)],
        environ={"RAMEN_API_KEY": FAKE_API_KEY},
        serve=serve_once(captured),
    )
    out = capsys.readouterr().out
    assert code == 0 and len(captured) == 1
    assert re.search(r"Open http://127\.0\.0\.1:\d+/", out)
    assert "local workcell checks only" in out and "not used in --local mode" in out
    assert FAKE_API_KEY not in out


def test_main_trial_and_keyed_modes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert (
        workcell_monitor.main(
            ["--trial", "--port", "0", "--run-dir", str(tmp_path / "t")],
            environ={"RAMEN_API_KEY": FAKE_API_KEY},
            client=server.trial_client(),
            serve=serve_once([]),
        )
        == 0
    )
    assert "keyless public trial" in capsys.readouterr().out
    assert (
        workcell_monitor.main(
            ["--keyed", "--port", "0", "--run-dir", str(tmp_path / "k")],
            environ={"RAMEN_API_KEY": FAKE_API_KEY},
            client=server.keyed_client(),
            serve=serve_once([]),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Mode: RAMEN_API_KEY" in out and FAKE_API_KEY not in out
    assert server.requests == []


def test_main_stops_cleanly_on_ctrl_c(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def interrupt(server: Any) -> None:
        raise KeyboardInterrupt

    assert (
        workcell_monitor.main(["--port", "0", "--run-dir", str(tmp_path)], environ={}, serve=interrupt) == 0
    )
    assert "stopped" in capsys.readouterr().out


def test_bad_base_url_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = workcell_monitor.main(
        ["--trial", "--run-dir", str(tmp_path)], environ={"RAMEN_BASE_URL": "http://api.ramenai.dev"}
    )
    assert code == 2 and "https" in capsys.readouterr().err


# -- assets and wording -----------------------------------------------------------


def test_static_assets_are_small_text_and_self_contained() -> None:
    sizes = load(".github/scripts/check_file_sizes.py")
    for path in ASSETS:
        data = path.read_bytes()
        assert len(data) < sizes.MAX_BYTES // 4, path
        assert path.suffix not in sizes.DENIED_SUFFIXES
        text = data.decode("utf-8")
        assert "http://" not in text and "https://" not in text, path
        assert ALL_INTERFACES not in text and "//cdn" not in text.lower(), path
        assert "@import" not in text and "url(http" not in text, path
    script = (DEMO / "hmi" / "hmi.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "eval(" not in script
    page = (DEMO / "hmi" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and " style=" not in page and "<style" not in page
    assert re.findall(r'(?:src|href)="([^"]+)"', page) == ["data:,", "/hmi.css", "/hmi.js"]
    assert hmi_http.TOKEN_PLACEHOLDER in page


def test_wording_rules_hold_in_every_new_file() -> None:
    hygiene = load(".github/scripts/check_hygiene.py")
    for path in NEW_FILES:
        text = path.read_text(encoding="utf-8")
        assert not hygiene.BRAND_PATTERN.search(text), path
        assert not hygiene.DISCLAIMER_PATTERN.search(text), path
        assert not re.search(r"(?i)collaborative robot|\bcobot", text), path
        assert not hygiene.PATH_PATTERN.search(text), path
