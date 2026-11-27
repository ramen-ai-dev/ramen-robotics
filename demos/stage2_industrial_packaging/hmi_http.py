"""HTTP layer of the Stage 2 workcell monitor: a pure router and a thin ``http.server`` adapter.

``HmiApp.handle`` maps ``(method, target, headers, body)`` to a ``Response`` with no
socket involved, so the routes are tested directly. ``make_handler`` adapts it to
``BaseHTTPRequestHandler`` and ``make_server`` binds a ``ThreadingHTTPServer`` to
127.0.0.1 only.

Every response carries a strict Content-Security-Policy (same-origin scripts and styles,
no inline code, no external hosts). Requests must name the bound host and port in
``Host``; state-changing POSTs also need the per-launch token from the page and, when the
browser sends one, a matching ``Origin``.
"""

from __future__ import annotations

import hmac
import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # repository root on sys.path (tests, editable installs)
    from demos.stage2_industrial_packaging.hmi_session import HmiSession, SessionError
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from hmi_session import HmiSession, SessionError  # type: ignore[no-redef]

BIND_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1024
TOKEN_HEADER = "x-hmi-token"  # noqa: S105 - header name, not a credential
TOKEN_PLACEHOLDER = "__HMI_TOKEN__"  # noqa: S105 - template marker
HMI_DIR = Path(__file__).resolve().parent / "hmi"
STATIC: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/hmi.css": ("hmi.css", "text/css; charset=utf-8"),
    "/hmi.js": ("hmi.js", "text/javascript; charset=utf-8"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)
_ROW_ROUTE = re.compile(r"^/api/rows/([1-9])/(cell|run)$")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = SECURITY_HEADERS

    def json(self) -> Any:
        return json.loads(self.body)


def json_response(status: int, payload: Any, *, extra: tuple[tuple[str, str], ...] = ()) -> Response:
    body = json.dumps(payload, default=str).encode("utf-8")
    return Response(status, body, headers=SECURITY_HEADERS + extra)


def error_response(
    status: int,
    code: str,
    detail: str,
    *,
    extra_fields: Mapping[str, Any] | None = None,
    headers: tuple[tuple[str, str], ...] = (),
) -> Response:
    return json_response(status, {"error": code, "detail": detail, **(extra_fields or {})}, extra=headers)


class HmiApp:
    """Route requests to an :class:`HmiSession`. Set ``port`` to the bound port before serving."""

    def __init__(self, session: HmiSession, *, token: str, port: int = 0, static_dir: Path = HMI_DIR) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self.session = session
        self.token = token
        self.port = port
        self._static = {
            name: (static_dir / name).read_bytes() for name in {name for name, _ in STATIC.values()}
        }
        self._get: dict[str, Callable[[], Any]] = {
            "/api/session": session.session_view,
            "/api/journal": session.journal_view,
            "/api/receipts": session.receipts_view,
            "/api/dossier": session.dossier_view,
        }
        self._post: dict[str, Callable[[], Any]] = {
            "/api/dossier/export": session.export,
            "/api/reset": session.reset,
        }

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"{BIND_HOST}:{self.port}", f"localhost:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(f"http://{host}" for host in self.allowed_hosts)

    def handle(self, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        del body  # no route reads a body; the adapter only bounds its size
        lowered = {key.lower(): value for key, value in headers.items()}
        if lowered.get("host") not in self.allowed_hosts:
            return error_response(403, "host_not_allowed", f"open http://{BIND_HOST}:{self.port}/")
        path = urlsplit(target).path
        if method == "POST":
            origin = lowered.get("origin")
            if origin is not None and origin not in self.allowed_origins:
                return error_response(403, "origin_not_allowed", "cross-origin requests are refused")
            supplied = lowered.get(TOKEN_HEADER, "").encode("utf-8")
            if not hmac.compare_digest(supplied, self.token.encode("utf-8")):
                return error_response(403, "token_required", "reload the monitor page")
        try:
            return self._route(method, path)
        except SessionError as error:
            return error_response(error.status, error.code, error.detail, extra_fields=error.extra)
        except Exception as error:  # never leak internals to the page
            print(f"workcell monitor: {type(error).__name__} on {method} {path}", file=sys.stderr)
            return error_response(500, "internal_error", type(error).__name__)

    def _route(self, method: str, path: str) -> Response:
        if path in STATIC:
            if method != "GET":
                return _not_allowed("GET")
            return self._static_response(path)
        match = _ROW_ROUTE.match(path)
        if match is not None:
            number, action = int(match.group(1)), match.group(2)
            wanted = "GET" if action == "cell" else "POST"
            if method != wanted:
                return _not_allowed(wanted)
            if action == "cell":
                return json_response(200, self.session.cell_view(number))
            return json_response(200, self.session.run_row(number))
        if path in self._get:
            return json_response(200, self._get[path]()) if method == "GET" else _not_allowed("GET")
        if path in self._post:
            return json_response(200, self._post[path]()) if method == "POST" else _not_allowed("POST")
        return error_response(404, "not_found", "no such route")

    def _static_response(self, path: str) -> Response:
        name, content_type = STATIC[path]
        body = self._static[name]
        if name == "index.html":
            body = body.replace(TOKEN_PLACEHOLDER.encode("ascii"), self.token.encode("ascii"))
        return Response(200, body, content_type)


def _not_allowed(allowed: str) -> Response:
    return error_response(405, "method_not_allowed", f"use {allowed}", headers=(("Allow", allowed),))


def make_handler(app: HmiApp) -> type[BaseHTTPRequestHandler]:
    """A request handler class bound to ``app``. Only GET and POST are implemented."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "workcell-monitor"

        def version_string(self) -> str:
            return self.server_version

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def _serve(self, method: str) -> None:
            if len(self.headers.get_all("Host") or []) > 1:
                self._write(error_response(400, "bad_request", "one Host header is allowed"))
                return
            raw = self.headers.get("Content-Length", "0")
            try:
                length = int(raw)
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                self._write(error_response(400, "bad_request", "invalid Content-Length"))
                return
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                self._write(
                    error_response(413, "body_too_large", f"bodies are limited to {MAX_BODY_BYTES} bytes")
                )
                return
            body = self.rfile.read(length) if length else b""
            self._write(app.handle(method, self.path, dict(self.headers.items()), body))

        def _write(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for name, value in response.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: Any) -> None:  # quiet: the page polls
            del format, args

    return Handler


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True


def make_server(app: HmiApp, port: int) -> MonitorServer:
    """Bind to 127.0.0.1:``port`` (0 picks a free port) and record the bound port on ``app``."""
    server = MonitorServer((BIND_HOST, port), make_handler(app))
    app.port = int(server.server_address[1])
    return server
