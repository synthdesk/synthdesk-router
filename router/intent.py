"""
Intent synthesis (pure, deterministic).

Frozen regime → intent mapping for router v0.1.
"""

from typing import Dict, List, Union


def intent_for_regime(regime: str) -> Dict[str, Union[str, float, List[str]]]:
    """
    Deterministic regime → intent mapping.

    Frozen v0.1 mapping. Changes require constitutional amendment.

    Args:
        regime: Regime classification string

    Returns:
        Intent dict with direction, size_pct, risk_cap, rationale
    """
    mapping = {
        "high_vol": {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["regime=high_vol"],
        },
        "chop": {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["regime=chop"],
        },
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

    # Unknown regime → flat (defensive default)
    if regime not in mapping:
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": [f"regime={regime} (unmapped)"],
        }

    return mapping[regime]
