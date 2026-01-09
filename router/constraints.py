"""
Constraint layer (veto logic).

Hard gates, not opinions.
Pure functions only.
"""

from enum import Enum
from typing import Dict, Optional, Union

from router.intent import intent_for_regime


class VetoReason(str, Enum):
    """
    Exhaustive veto reasons. Frozen.

    When router declines to emit intent, exactly one of these applies.
    No extensibility without constitutional amendment.
    """

    INVARIANT_VIOLATION = "invariant_violation"  # system unsafe
    INPUT_UNAVAILABLE = "input_unavailable"  # missing or invalid inputs
    REGIME_UNRESOLVED = "regime_unresolved"  # router cannot derive exposure from regime state


def evaluate_constraints(
    state_dict: Dict,
    symbol: str,
) -> Union[Dict, VetoReason]:
    """
    Evaluate constraints and return intent or veto reason.

    Returns exactly one of:
    - Intent dict (positive exposure authority)
    - VetoReason enum member (typed silence)

    Never both. Never None. Never "flat intent".

    Pure function. No side effects.

    Args:
        state_dict: Router state as dict (for pure function compatibility)
        symbol: Symbol identifier

    Returns:
        Intent dict OR VetoReason enum member
    """
    system = state_dict.get("system", {})
    symbols = state_dict.get("symbols", {})

    # Hard veto: invariant violation active
    if system.get("violation_active"):
        return VetoReason.INVARIANT_VIOLATION

    # Hard veto: listener down / missing inputs
    if not system.get("listener_alive"):
        return VetoReason.INPUT_UNAVAILABLE

    # Check regime state
    symbol_state = symbols.get(symbol, {})
    regime = symbol_state.get("regime")

    # Hard veto: regime unknown
    if regime is None:
        return VetoReason.REGIME_UNRESOLVED

    # Attempt intent synthesis
    intent = intent_for_regime(regime)

    # Veto: regime does not resolve to exposure (high_vol, chop, unknown)
    if intent is None:
        return VetoReason.REGIME_UNRESOLVED

    # No veto → return intent
    return intent


def should_emit_intent(current_intent: Dict, last_intent: Optional[Dict]) -> bool:
    """
    Check if intent changed (deduplication).

    Only emit on change to prevent spam.

    Args:
        current_intent: New intent to potentially emit
        last_intent: Last emitted intent (or None)

    Returns:
        True if intent changed, False if duplicate
    """
    if last_intent is None:
        return True

    # Compare direction, size_pct, risk_cap (ignore rationale for dedup)
    return (
        current_intent.get("direction") != last_intent.get("direction")
        or current_intent.get("size_pct") != last_intent.get("size_pct")
        or current_intent.get("risk_cap") != last_intent.get("risk_cap")
    )
