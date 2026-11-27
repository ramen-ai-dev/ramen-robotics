"""Recipe and process-data egress: deny by default, allow only listed destinations."""

from __future__ import annotations

from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import RecipeExport, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode


def _normalize(destination: str) -> str:
    return destination.strip().rstrip("/").lower()


class EgressCheck:
    name = "egress"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, RecipeExport)

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, RecipeExport)
        policy = profile.egress
        destination = _normalize(request.destination)
        allowed = {_normalize(item) for item in policy.allowed_destinations}
        classified_ok = {_normalize(item) for item in policy.classified_destinations}
        findings: list[Finding] = []
        if destination not in allowed:
            findings.append(
                Finding(FindingCode.EGRESS_DENIED, self.name, "destination is not on the egress allowlist")
            )
        classified = sorted(set(request.fields) & policy.classified_fields)
        if classified and destination not in classified_ok:
            findings.append(
                Finding(
                    FindingCode.EGRESS_DENIED,
                    self.name,
                    f"classified fields ({', '.join(classified)}) may not go to this destination",
                )
            )
        if request.recipe_id not in profile.allergen_matrix:
            findings.append(
                Finding(FindingCode.EGRESS_DENIED, self.name, "recipe is not declared for this cell")
            )
        return tuple(findings)
