"""Release guard: the last check between a verified ALLOW and the host tool.

``IndustrialAutomationAgent`` runs the registered tool as soon as it has a verified
ALLOW. :class:`ReleaseGuard` is registered in place of the host tool, so the gate
gets a final say just before release: the deadline must not have passed, and the
gate's own receipt check must agree with the verdict. If either check fails the
guard raises :class:`HoldSignal`, ramen foundry reports a tool failure, the host
tool never runs, and the gate reports a hold.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict


class HoldSignal(RuntimeError):
    """Raised by a release check to stop a release. ``kind`` names the hold outcome."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class ReleaseGuard(BaseTool):
    """Wrap a host tool so it runs only after ``check`` passes.

    ``check`` raises :class:`HoldSignal` to hold. ``on_release`` is called just before
    the host tool runs and ``on_error`` if the host tool raises.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    host: BaseTool
    check: Callable[[], None]
    on_release: Callable[[], None]
    on_error: Callable[[BaseException], None]

    @classmethod
    def wrap(
        cls,
        host: BaseTool,
        *,
        check: Callable[[], None],
        on_release: Callable[[], None],
        on_error: Callable[[BaseException], None],
    ) -> ReleaseGuard:
        return cls(
            name=host.name,
            description=host.description,
            host=host,
            check=check,
            on_release=on_release,
            on_error=on_error,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - invoke() is overridden
        raise NotImplementedError("ReleaseGuard is invoked through invoke()")

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self.check()
        self.on_release()
        try:
            return self.host.invoke(input, config, **kwargs)
        except BaseException as error:
            self.on_error(error)
            raise
