"""Stage 2 workcell monitor: a localhost browser view of CELL-PACK-03 under rush-order pressure.

Serves a control-room page on 127.0.0.1 that steps through the nine rush-order requests
of the Stage 2 demo, draws the collaborative application, shows each workcell gate
decision with its finding codes, steering directive and Schema V5 receipt, and exports
and re-verifies the audit dossier. Standard library HTTP server, no external assets.

    python demos/stage2_industrial_packaging/workcell_monitor.py                 # --local: rows 1-6, 0 requests
    python demos/stage2_industrial_packaging/workcell_monitor.py --trial         # rows 7-9 live on click, keyless
    RAMEN_API_KEY=... python demos/stage2_industrial_packaging/workcell_monitor.py --keyed

``--local`` and ``--trial`` never read or send ``RAMEN_API_KEY``, even when it is set.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ramen_robotics.gate import GateConfigError, describe_mode

try:  # repository root on sys.path (tests, editable installs)
    from demos.stage2_industrial_packaging.hmi_http import BIND_HOST, HmiApp, MonitorServer, make_server
    from demos.stage2_industrial_packaging.hmi_session import (
        DEFAULT_RUN_ROOT,
        LOCAL_MODE_LINE,
        HmiSession,
        Mode,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from hmi_http import BIND_HOST, HmiApp, MonitorServer, make_server  # type: ignore[no-redef]
    from hmi_session import DEFAULT_RUN_ROOT, LOCAL_MODE_LINE, HmiSession, Mode  # type: ignore[no-redef]

API_KEY_ENV = "RAMEN_API_KEY"
DEFAULT_PORT = 8765


class ModeError(ValueError):
    """The requested mode cannot run in this environment."""


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--port takes a number from 0 to 65535") from error
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("--port takes a number from 0 to 65535")
    return port


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 2 workcell monitor for CELL-PACK-03, served on 127.0.0.1"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--local",
        dest="mode",
        action="store_const",
        const="local",
        help="default: rows 1-6 run locally, rows 7-9 are not evaluated (0 requests)",
    )
    modes.add_argument(
        "--trial",
        dest="mode",
        action="store_const",
        const="trial",
        help="rows 7-9 run live on click against the keyless public trial (RAMEN_API_KEY is not read)",
    )
    modes.add_argument(
        "--keyed",
        dest="mode",
        action="store_const",
        const="keyed",
        help="rows 7-9 run live on click with RAMEN_API_KEY (production receipts)",
    )
    parser.set_defaults(mode="local")
    parser.add_argument(
        "--port", type=parse_port, default=DEFAULT_PORT, help="port on 127.0.0.1 (0 = any free port)"
    )
    parser.add_argument("--open", action="store_true", help="open the monitor in the default browser")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_ROOT,
        help="parent of the per-run journal, receipts and dossier directories",
    )
    parser.add_argument(
        "--allow-trial-dossier",
        action="store_true",
        help="allow exporting trial receipts, labeled evidence_class: trial",
    )
    return parser.parse_args(argv)


def mode_environment(mode: Mode, environ: Mapping[str, str]) -> dict[str, str]:
    """The environment the gate sees: the key only in keyed mode, and keyed mode only with a key."""
    env = dict(environ)
    if mode == "keyed":
        if not env.get(API_KEY_ENV, "").strip():
            raise ModeError(f"--keyed needs {API_KEY_ENV} in the environment")
        return env
    env.pop(API_KEY_ENV, None)
    return env


def _serve_forever(server: MonitorServer) -> None:
    server.serve_forever(poll_interval=0.5)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    client: Any = None,
    gate_options: Mapping[str, Any] | None = None,
    serve: Callable[[MonitorServer], None] | None = None,
) -> int:
    arguments = parse_arguments(argv)
    source = os.environ if environ is None else environ
    mode: Mode = arguments.mode
    try:
        env = mode_environment(mode, source)
        session = HmiSession(
            mode,
            environ=env,
            run_root=arguments.run_dir,
            client=client,
            gate_options=gate_options,
            allow_trial_dossier=arguments.allow_trial_dossier,
        )
    except (ModeError, GateConfigError) as error:
        print(f"workcell monitor: {error}", file=sys.stderr)
        return 2
    try:
        app = HmiApp(session, token=secrets.token_urlsafe(32))
        try:
            server = make_server(app, arguments.port)
        except OSError as error:
            print(
                f"workcell monitor: could not listen on {BIND_HOST}:{arguments.port} ({error.strerror}); "
                "try --port 0",
                file=sys.stderr,
            )
            return 1
        url = f"http://{BIND_HOST}:{app.port}/"
        print(f"{session.profile.cell_id} workcell monitor")
        print(LOCAL_MODE_LINE if mode == "local" else describe_mode(session.gate))
        if mode != "keyed" and source.get(API_KEY_ENV, "").strip():
            print(f"{API_KEY_ENV} is set and not used in --{mode} mode; pass --keyed to use it")
        print(f"Run directory: {session.run_dir}")
        print(f"Open {url}  (Ctrl+C to stop)", flush=True)
        if arguments.open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        try:
            (serve or _serve_forever)(server)
        except KeyboardInterrupt:
            print("\nworkcell monitor stopped")
        finally:
            server.server_close()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
