"""Base class for MuJoCo robotics demos.

Loads a scene and, when asked, opens the passive MuJoCo viewer. With a viewer the
simulation is paced to wall-clock time so motion is watchable, and ``close`` keeps
the window up until the user closes it. Headless runs step as fast as possible.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# If wall-clock time runs this far ahead of simulated time (a slow network call
# between motions, a stalled render), re-anchor instead of fast-forwarding.
_MAX_LAG_S = 0.1


class Demo:
    def __init__(self, scene_path: Path, *, viewer: bool = False) -> None:
        try:
            import mujoco
        except ImportError as error:  # pragma: no cover - install environment
            raise SystemExit('Install the simulator: pip install -e ".[simulation]"') from error

        self.mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(scene_path))
        self.data = mujoco.MjData(self.model)

        self.viewer: Any = None
        self._wall_anchor = 0.0
        self._sim_anchor = 0.0
        if viewer:  # pragma: no cover - interactive
            import mujoco.viewer

            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
            self._reanchor()

    def sync(self) -> None:
        """Push the current state to the viewer, sleeping so the sim runs in real time."""
        if self.viewer is not None:  # pragma: no cover - interactive
            lag = (self.data.time - self._sim_anchor) - (time.perf_counter() - self._wall_anchor)
            if lag > 0:
                time.sleep(lag)
            elif lag < -_MAX_LAG_S:
                self._reanchor()
            self.viewer.sync()

    def close(self) -> None:
        """Wait for the user to close the viewer window, then release it."""
        if self.viewer is not None:  # pragma: no cover - interactive
            print("Close the viewer window to exit.")
            while self.viewer.is_running():
                time.sleep(0.05)
            self.viewer.close()

    def _reanchor(self) -> None:
        self._wall_anchor = time.perf_counter()
        self._sim_anchor = self.data.time
