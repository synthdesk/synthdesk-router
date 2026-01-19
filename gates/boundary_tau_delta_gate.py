"""
Boundary τ/Δ Gate — Provisional

This gate enforces the provisional governance default:
τ = 15, Δ = 15

It does not select, score, rank, or promote intents.
It only filters eligibility for interior consideration.

Status:
- Inert
- Read-only
- Non-authoritative
"""

from dataclasses import dataclass
from typing import Optional


PROVISIONAL_TAU = 15
PROVISIONAL_DELTA = 15


@dataclass(frozen=True)
class BoundaryParams:
    tau: int
    delta: int


def boundary_params_are_eligible(
    params: BoundaryParams,
) -> bool:
    """
    Returns True iff the boundary parameters match
    the provisional governance default.

    This is a hard gate, not a preference.
    """
    return (
        params.tau == PROVISIONAL_TAU
        and params.delta == PROVISIONAL_DELTA
    )


def gate_reason(params: BoundaryParams) -> Optional[str]:
    """
    Returns a human-readable reason for ineligibility.
    """
    if params.tau != PROVISIONAL_TAU:
        return f"ineligible: tau={params.tau} (provisional default is {PROVISIONAL_TAU})"

    if params.delta != PROVISIONAL_DELTA:
        return f"ineligible: delta={params.delta} (provisional default is {PROVISIONAL_DELTA})"

    return None
