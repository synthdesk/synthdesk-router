"""
Constraint layer (veto logic).

Hard gates, not opinions.
Pure functions only.

Surface invariants (defense-in-depth):
- Flat direction → veto (no "flat intent")
- Zero size with non-flat direction → veto
- Invalid risk_cap for v0.2 → veto
- Empty rationale → add deterministic rationale

Intent strength classification:
- Weak intent (C_WEAK_MIN ≤ confidence < C_STRONG_MIN): question with directional hypothesis
- Strong intent (confidence ≥ C_STRONG_MIN): decision with execution authority
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


# ---------------------------
# Confidence Thresholds (Frozen)
# ---------------------------

# Minimum confidence for weak intent emission
# Below this: no edge detected, veto
C_WEAK_MIN: float = 0.15

# Minimum confidence for strong intent emission
# Below this but ≥ C_WEAK_MIN: weak intent (question)
# At or above: strong intent (decision)
C_STRONG_MIN: float = 0.50


class VetoReason(str, Enum):
    """
    Exhaustive veto reasons. Frozen.

    When router declines to emit intent, exactly one of these applies.
    No extensibility without constitutional amendment.

    Semantic categories:
    - System vetoes: infrastructure issues, always "honest" by construction
    - Edge-absent vetoes: no market signal detected, abstention (not chaos claim)
    - Danger vetoes: market conditions unsafe, should correlate with chaos
    - Surface vetoes: veto surface blocks based on time-scale (DOCTRINE: VETO_TIMESCALE)
    """

    # System vetoes (infrastructure)
    INVARIANT_VIOLATION = "invariant_violation"  # system unsafe
    INPUT_UNAVAILABLE = "input_unavailable"  # missing or invalid inputs
    AUTHORITY_GATE = "authority_gate"  # v0.1 blocks non-flat posture

    # Edge-absent vetoes (abstention, not chaos claim)
    REGIME_UNRESOLVED = "regime_unresolved"  # router cannot derive exposure from regime state
    NO_EDGE = "no_edge"  # chop regime: no directional signal detected
    CONFIDENCE_TOO_LOW = "confidence_too_low"  # confidence < C_WEAK_MIN

    # Danger vetoes (should correlate with chaos)
    REGIME_VOLATILE = "regime_volatile"  # high_vol regime: excess risk

    # Surface vetoes (DOCTRINE: VETO_TIMESCALE, added 2026-01-23)
    # Time-scale aware blocking based on veto surfaces
    SURFACE_REGIME_BLOCK = "surface_regime_block"  # regime surface blocks long holds
    SURFACE_MICRO_BLOCK = "surface_micro_block"  # micro surface blocks short holds
    SURFACE_REGIME_STALE = "surface_regime_stale"  # regime surface STALE/ERROR (fail-closed)
    SURFACE_REGIME_MISSING = "surface_regime_missing"  # regime surface not found (fail-closed)
    SURFACE_MICRO_STALE = "surface_micro_stale"  # micro surface STALE/ERROR (P_UNION_SAFETY)
    SURFACE_MISSING_HOLD = "surface_missing_hold"  # intended_hold_min not provided (HARD BLOCK)


class IntentStrength(str, Enum):
    """
    Intent strength classification.

    Weak intents are questions with directional hypothesis.
    Strong intents are decisions with execution authority.
    """

    WEAK = "weak"    # C_WEAK_MIN ≤ confidence < C_STRONG_MIN
    STRONG = "strong"  # confidence ≥ C_STRONG_MIN


# Map allocator veto reasons to constitutional VetoReason
_VETO_REASON_MAP = {
    "input_unavailable": VetoReason.INPUT_UNAVAILABLE,
    "symbol_degraded": VetoReason.INPUT_UNAVAILABLE,  # Non-critical violation, auto-recovers
    "regime_unresolved": VetoReason.REGIME_UNRESOLVED,
    "regime_chop": VetoReason.NO_EDGE,  # No edge = abstention, not chaos claim
    "regime_high_vol": VetoReason.REGIME_VOLATILE,  # Danger = should correlate with chaos
    # Note: Critical violations trigger authority demotion (not allocator veto)
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


def classify_intent_strength(confidence: float) -> Optional[IntentStrength]:
    """
    Classify intent strength based on confidence.

    Args:
        confidence: Regime confidence from classifier [0, 1]

    Returns:
        IntentStrength.WEAK if C_WEAK_MIN ≤ confidence < C_STRONG_MIN
        IntentStrength.STRONG if confidence ≥ C_STRONG_MIN
        None if confidence < C_WEAK_MIN (should veto)
    """
    if confidence < C_WEAK_MIN:
        return None  # Below threshold, veto
    elif confidence < C_STRONG_MIN:
        return IntentStrength.WEAK
    else:
        return IntentStrength.STRONG


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


def evaluate_constraints_with_strength(
    state_dict: Dict,
    symbol: str,
) -> tuple[Union[AllocationResult, VetoReason], Optional[IntentStrength]]:
    """
    Evaluate constraints and return allocation/veto with intent strength.

    Extended version of evaluate_constraints that also classifies intent strength
    based on regime confidence.

    Returns:
        (result, intent_strength) where:
        - result is AllocationResult or VetoReason
        - intent_strength is WEAK, STRONG, or None (if veto)

    Intent strength classification:
    - confidence < C_WEAK_MIN → CONFIDENCE_TOO_LOW veto
    - C_WEAK_MIN ≤ confidence < C_STRONG_MIN → WEAK intent
    - confidence ≥ C_STRONG_MIN → STRONG intent
    """
    allocation, veto_reason = compute_allocation_from_state(state_dict, symbol)

    if veto_reason:
        # Map to constitutional veto reason
        return (_VETO_REASON_MAP.get(veto_reason, VetoReason.REGIME_UNRESOLVED), None)

    # Flat allocation is also a veto (no "flat intent")
    if allocation.direction == Direction.FLAT:
        return (VetoReason.REGIME_UNRESOLVED, None)

    # Surface invariant validation (defense-in-depth)
    surface_error = _validate_allocation_surface(allocation)
    if surface_error:
        # Surface violation → veto (fail closed)
        return (VetoReason.REGIME_UNRESOLVED, None)

    # Classify intent strength based on confidence
    # entropy_factor represents the confidence used in allocation
    confidence = allocation.entropy_factor
    strength = classify_intent_strength(confidence)

    if strength is None:
        # Confidence too low → veto
        return (VetoReason.CONFIDENCE_TOO_LOW, None)

    # Return allocation with strength classification
    return (allocation, strength)


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
