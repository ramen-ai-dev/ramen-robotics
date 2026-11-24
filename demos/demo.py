"""Base class for MuJoCo robotics demos.

A demo steps its own physics, calling ``sync()`` after each ``mj_step`` and ``mark()``
when a new phase begins. Headless, both are no-ops. With ``viewer=True`` they record
frames, and ``close()`` opens the MuJoCo viewer on a replay of the run:

    Space / Run-Pause button   play or pause
    Backspace / Reset button   restart from the beginning
    Left / Right arrows        step one frame while paused

The run itself happens once, at full speed, before the window opens. Replaying never
steps physics or repeats a gate request.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRAME_RATE_HZ = 60.0
LEAD_IN_S = 1.0  # hold the first frame this long after the window opens or restarts

_KEY_RIGHT, _KEY_LEFT, _KEY_BACKSPACE = 262, 263, 259  # GLFW key codes
# Replayed frames carry this offset on data.time so a viewer Reset (which zeroes it) is detectable.
_TIME_MARK = 1e-6


@dataclass(frozen=True)
class Frame:
    time: float
    qpos: Any
    phase: str


class Demo:
    def __init__(self, scene_path: Path, *, viewer: bool = False) -> None:
        try:
            import mujoco
        except ImportError as error:  # pragma: no cover - install environment
            raise SystemExit('Install the simulator: pip install -e ".[simulation]"') from error

        self.mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(scene_path))
        self.data = mujoco.MjData(self.model)

        self.record = viewer
        self.frames: list[Frame] = []
        self.phase = ""
        self.summary = ""  # shown in the replay's top-right corner; set it once the outcome is known

    def mark(self, phase: str) -> None:
        """Start a named phase; the replay overlay shows it on every frame that follows."""
        self.phase = phase
        if self.record:
            self._capture()

    def sync(self) -> None:
        """Record the current state if a frame is due. Call after each ``mj_step``."""
        if self.record and (not self.frames or self.data.time - self.frames[-1].time >= 1 / FRAME_RATE_HZ):
            self._capture()

    def close(self) -> None:
        """Replay the recorded run in the viewer until the window is closed."""
        if self.record and self.frames:  # pragma: no cover - interactive
            self._replay()

    def _capture(self) -> None:
        self.frames.append(Frame(float(self.data.time), self.data.qpos.copy(), self.phase))

    def _replay(self) -> None:  # pragma: no cover - interactive
        import mujoco.viewer

        mujoco = self.mujoco
        frames, times = self.frames, [frame.time for frame in self.frames]
        start, end = times[0], times[-1]
        data = mujoco.MjData(self.model)
        state = {"clock": start, "lead_in": LEAD_IN_S, "restart": False, "step": 0}

        def on_key(key: int) -> None:
            if key == _KEY_BACKSPACE:
                state["restart"] = True
            elif key in (_KEY_LEFT, _KEY_RIGHT):
                state["step"] += 1 if key == _KEY_RIGHT else -1

        viewer = mujoco.viewer.launch_passive(self.model, data, key_callback=on_key)
        print(
            f"Replaying {end - start:.2f} s ({len(frames)} frames). Space plays or pauses, Backspace restarts."
        )
        print("Close the viewer window to exit.")
        last = time.perf_counter()
        while viewer.is_running():
            now = time.perf_counter()
            elapsed, last = now - last, now
            if state["restart"]:
                state.update(clock=start, lead_in=LEAD_IN_S, restart=False, step=0)
            running = _viewer_running(viewer)
            index = bisect.bisect_right(times, state["clock"]) - 1
            if not running:
                index = min(max(index + state["step"], 0), len(frames) - 1)
                state["clock"], state["step"] = times[index], 0
            elif state["lead_in"] > 0:
                state["lead_in"] -= elapsed
            else:
                state["clock"] = min(state["clock"] + elapsed, end)
            frame = frames[max(index, 0)]

            data.qpos[:] = frame.qpos
            data.qvel[:] = 0.0
            data.time = frame.time + _TIME_MARK
            mujoco.mj_forward(self.model, data)
            status = "playing" if running else "paused"
            if running and state["clock"] >= end:
                status = "end, Backspace to replay"
            viewer.set_texts(
                [
                    (
                        None,
                        mujoco.mjtGridPos.mjGRID_TOPLEFT,
                        frame.phase,
                        f"t = {frame.time:.2f} s  ({status})",
                    ),
                    (None, mujoco.mjtGridPos.mjGRID_TOPRIGHT, self.summary, None),
                    (
                        mujoco.mjtFontScale.mjFONTSCALE_100,
                        mujoco.mjtGridPos.mjGRID_BOTTOMLEFT,
                        "Space: play/pause\nBackspace: restart\nLeft/Right: step (paused)",
                        None,
                    ),
                ]
            )
            viewer.sync()
            if data.time == 0.0:  # the viewer's own Reset zeroed the replay state
                state["restart"] = True
            time.sleep(1 / FRAME_RATE_HZ)
        viewer.close()


def _viewer_running(viewer: Any) -> bool:  # pragma: no cover - interactive
    """Mirror the viewer's own Run/Pause state (Space or the UI button)."""
    sim = viewer._get_sim()  # mujoco 3.13 exposes no public accessor for the run flag
    return True if sim is None else bool(sim.run)
