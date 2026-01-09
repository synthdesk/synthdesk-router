"""
Intent synthesis (pure, deterministic).

Frozen regime → intent mapping for router v0.1.

Intent = positive exposure authority.
Regimes that produce no exposure must veto, not emit "flat intent".
"""

from typing import Dict, List, Optional, Union


# Regimes that produce positive intent (exposure authority)
INTENT_REGIMES = {
    "drift": {
        "direction": "long",
        "size_pct": 0.25,
        "risk_cap": "normal",
        "rationale": ["regime=drift"],
    },
    "breakout": {
        "direction": "long",
        "size_pct": 0.25,
        "risk_cap": "high",
        "rationale": ["regime=breakout"],
    },
}

def intent_for_regime(regime: str) -> Optional[Dict[str, Union[str, float, List[str]]]]:
    """
    Deterministic regime → intent mapping.

    Frozen v0.1 mapping. Changes require constitutional amendment.

    Returns None for regimes that should veto (high_vol, chop, unknown).
    Intent = positive exposure. No "flat intent" exists.

    Args:
        regime: Regime classification string

    Returns:
        Intent dict with direction, size_pct, risk_cap, rationale
        OR None if regime should produce veto
    """
    return INTENT_REGIMES.get(regime)
