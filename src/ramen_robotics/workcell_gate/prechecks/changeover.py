"""Hygiene and changeover: recipe loaded, tooling matched, allergen cleaning verified."""

from __future__ import annotations

from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import BatchStart, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode
from .evidence import verify_token


class ChangeoverCheck:
    """Hold a batch start unless the line is set up and cleaned for the requested recipe.

    A sanitation token is required whenever the previous recipe declared an allergen the
    next recipe does not. It must come from a pinned sanitation issuer and bind the cell,
    both recipe ids and ``swab_result == "PASS"``.
    """

    name = "changeover"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, BatchStart)

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, BatchStart)
        findings: list[Finding] = []
        recipe = request.recipe_id
        if recipe not in profile.allergen_matrix:
            findings.append(
                Finding(
                    FindingCode.ALLERGEN_CONFLICT, self.name, "recipe is not declared in the allergen matrix"
                )
            )
        if snapshot.active_recipe_id != recipe:
            findings.append(
                Finding(
                    FindingCode.RECIPE_MISMATCH, self.name, "recipe loaded on the line differs from the order"
                )
            )
        permitted = profile.recipe_tooling.get(recipe, frozenset())
        if snapshot.mounted_gripper_id not in permitted:
            findings.append(
                Finding(
                    FindingCode.TOOLING_MISMATCH,
                    self.name,
                    f"mounted gripper {snapshot.mounted_gripper_id} is not permitted for recipe {recipe} "
                    f"(permitted: {', '.join(sorted(permitted)) or 'none'})",
                )
            )
        previous = snapshot.previous_recipe_id
        if previous is not None and previous != recipe and recipe in profile.allergen_matrix:
            if previous not in profile.allergen_matrix:
                findings.append(
                    Finding(
                        FindingCode.ALLERGEN_CONFLICT,
                        self.name,
                        "previous recipe is not declared in the allergen matrix",
                    )
                )
            else:
                carried = profile.allergen_matrix[previous] - profile.allergen_matrix[recipe]
                if carried:
                    valid, reason = verify_token(
                        snapshot.sanitation_token,
                        profile.sanitation_issuers,
                        {
                            "cell_id": profile.cell_id,
                            "previous_recipe_id": previous,
                            "next_recipe_id": recipe,
                            "swab_result": "PASS",
                        },
                    )
                    if not valid:
                        findings.append(
                            Finding(
                                FindingCode.SANITATION_UNVERIFIED,
                                self.name,
                                f"allergen changeover ({', '.join(sorted(carried))}) without verified "
                                f"cleaning clearance: {reason}",
                            )
                        )
        return tuple(findings)
