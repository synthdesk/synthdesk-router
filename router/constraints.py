"""
Constraint layer (veto logic).

Hard gates, not opinions.
Pure functions only.
"""

from typing import Dict, List, Optional, Union

from router.intent import intent_for_regime


def apply_constraints(
    state_dict: Dict,
    symbol: str,
) -> Dict[str, Union[str, float, List[str]]]:
    """
    Apply veto constraints and synthesize intent.

    Hard veto conditions (VETO_MATRIX.md):
    - invariant.violation seen → force flat
    - listener.crash recent → force flat
    - regime unresolved → force flat

    Pure function. No side effects.

    Args:
        state_dict: Router state as dict (for pure function compatibility)
        symbol: Symbol identifier

    Returns:
        Intent dict (direction, size_pct, risk_cap, rationale)
    """
    system = state_dict.get("system", {})
    symbols = state_dict.get("symbols", {})

    # Hard veto: invariant violation active
    if system.get("violation_active"):
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["invariant.violation active"],
        }

    # Hard veto: listener crashed
    if not system.get("listener_alive"):
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["listener.crash detected"],
        }

    # Hard veto: regime unknown
    symbol_state = symbols.get(symbol, {})
    regime = symbol_state.get("regime")
    if regime is None:
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["regime unresolved"],
        }

    # No veto → synthesize intent from regime
    intent = intent_for_regime(regime)
    rationale = list(intent.get("rationale", []))
    rationale.extend(["no violations", "listener alive"])
    intent["rationale"] = rationale
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
