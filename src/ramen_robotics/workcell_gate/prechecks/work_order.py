"""Work-order authenticity: a batch starts only from an MES or ERP order signed by a pinned issuer."""

from __future__ import annotations

from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import BatchStart, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode
from .evidence import MISMATCH_MARKER, verify_token


class WorkOrderCheck:
    """The token must bind cell, order, recipe, lot and quantity exactly as requested."""

    name = "work_order"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, BatchStart)

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, BatchStart)
        valid, reason = verify_token(
            request.work_order_token,
            profile.work_order_issuers,
            {
                "cell_id": profile.cell_id,
                "work_order_id": request.work_order_id,
                "recipe_id": request.recipe_id,
                "lot_id": request.lot_id,
                "quantity": request.quantity,
            },
        )
        if valid:
            return ()
        code = (
            FindingCode.WORK_ORDER_MISMATCH
            if reason and MISMATCH_MARKER in reason
            else FindingCode.WORK_ORDER_UNVERIFIED
        )
        return (Finding(code, self.name, reason or "work order did not verify"),)
