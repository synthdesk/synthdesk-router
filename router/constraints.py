"""
Constraint layer (veto logic).

Hard gates, not opinions.
Pure functions only.

Surface invariants (defense-in-depth):
- Flat direction → veto (no "flat intent")
- Zero size with non-flat direction → veto
- Invalid risk_cap for v0.2 → veto
- Empty rationale → add deterministic rationale
"""

from enum import Enum
from typing import Dict, Optional, Union

from router.allocator import (
    AllocationResult,
    Direction,
    RiskCap,
    SIZE_PCT_SCALE,
    compute_allocation_from_state,
)


class VetoReason(str, Enum):
    """
    Exhaustive veto reasons. Frozen.

    When router declines to emit intent, exactly one of these applies.
    No extensibility without constitutional amendment.

    Semantic categories:
    - System vetoes: infrastructure issues, always "honest" by construction
    - Edge-absent vetoes: no market signal detected, abstention (not chaos claim)
    - Danger vetoes: market conditions unsafe, should correlate with chaos
    """

    # System vetoes (infrastructure)
    INVARIANT_VIOLATION = "invariant_violation"  # system unsafe
    INPUT_UNAVAILABLE = "input_unavailable"  # missing or invalid inputs
    AUTHORITY_GATE = "authority_gate"  # v0.1 blocks non-flat posture

    # Edge-absent vetoes (abstention, not chaos claim)
    REGIME_UNRESOLVED = "regime_unresolved"  # router cannot derive exposure from regime state
    NO_EDGE = "no_edge"  # chop regime: no directional signal detected

    # Danger vetoes (should correlate with chaos)
    REGIME_VOLATILE = "regime_volatile"  # high_vol regime: excess risk


# Map allocator veto reasons to constitutional VetoReason
_VETO_REASON_MAP = {
    "input_unavailable": VetoReason.INPUT_UNAVAILABLE,
    "violation_active": VetoReason.INVARIANT_VIOLATION,
    "regime_unresolved": VetoReason.REGIME_UNRESOLVED,
    "regime_chop": VetoReason.NO_EDGE,  # No edge = abstention, not chaos claim
    "regime_high_vol": VetoReason.REGIME_VOLATILE,  # Danger = should correlate with chaos
}

# v0.2 permitted risk caps (constitutional)
_V02_PERMITTED_RISK_CAPS = {RiskCap.ZERO, RiskCap.LOW, RiskCap.MEDIUM}


def _validate_allocation_surface(allocation: AllocationResult) -> Optional[str]:
    """
    Validate allocation meets v0.2 surface invariants.

    Returns None if valid, else error description.
    """
    # Invariant: Non-flat direction with zero size is invalid
    if allocation.direction != Direction.FLAT and allocation.size_pct_q == 0:
        return "zero_size_non_flat"

    # Invariant: risk_cap must be v0.2 permitted
    if allocation.risk_cap not in _V02_PERMITTED_RISK_CAPS:
        return f"invalid_risk_cap:{allocation.risk_cap.value}"

    # Invariant: size_pct_scale must be canonical
    if allocation.size_pct_scale != SIZE_PCT_SCALE:
        return f"invalid_scale:{allocation.size_pct_scale}"

    # Invariant: rationale must not be empty
    if not allocation.rationale:
        return "empty_rationale"

    return None


def evaluate_constraints(
    state_dict: Dict,
    symbol: str,
) -> Union[AllocationResult, VetoReason]:
    """
    Evaluate constraints and return allocation or veto reason.

    Returns exactly one of:
    - AllocationResult (positive exposure authority with quantized sizing)
    - VetoReason enum member (typed silence)

    Never both. Never None. Never "flat intent".

    Pure function. No side effects.

    Surface invariants enforced:
    - Flat direction → veto
    - Zero size with non-flat direction → veto
    - Invalid risk_cap → veto
    - Invalid scale → veto

    Args:
        state_dict: Router state as dict (for pure function compatibility)
        symbol: Symbol identifier

    Returns:
        AllocationResult OR VetoReason enum member
    """
    allocation, veto_reason = compute_allocation_from_state(state_dict, symbol)

    if veto_reason:
        # Map to constitutional veto reason
        return _VETO_REASON_MAP.get(veto_reason, VetoReason.REGIME_UNRESOLVED)

    # Flat allocation is also a veto (no "flat intent")
    if allocation.direction == Direction.FLAT:
        return VetoReason.REGIME_UNRESOLVED

    # Surface invariant validation (defense-in-depth)
    surface_error = _validate_allocation_surface(allocation)
    if surface_error:
        # Surface violation → veto (fail closed)
        return VetoReason.REGIME_UNRESOLVED

    # No veto → return allocation
    return allocation


def should_emit_intent(
    current: AllocationResult,
    last: Optional[AllocationResult],
) -> bool:
    """
    Check if allocation changed (deduplication).

    Only emit on change to prevent spam.

    Args:
        current: New allocation to potentially emit
        last: Last emitted allocation (or None)

    Returns:
        True if allocation changed, False if duplicate
    """
    if last is None:
        return True

    # Compare direction, quantized size, risk_cap (ignore rationale for dedup)
    return (
        current.direction != last.direction
        or current.size_pct_q != last.size_pct_q
        or current.risk_cap != last.risk_cap
    )
