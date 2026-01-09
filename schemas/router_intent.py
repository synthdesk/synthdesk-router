"""Schema + validation for router.intent and router.veto."""

from __future__ import annotations

import math
from typing import Any

# Intent fields
DIRECTIONS = {"long", "short"}  # No "flat" - flat is veto, not intent
RISK_CAPS = {"low", "normal", "high"}

# Veto reasons (frozen, exhaustive)
VETO_REASONS = {
    "invariant_violation",
    "input_unavailable",
    "regime_unresolved",
}


def validate_router_intent(payload: Any) -> None:
    """Validate router.intent payload."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    direction = payload.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError("invalid direction")

    size_pct = payload.get("size_pct")
    if not isinstance(size_pct, (int, float)) or isinstance(size_pct, bool):
        raise ValueError("size_pct invalid")
    if not math.isfinite(size_pct) or size_pct < 0.0:
        raise ValueError("size_pct invalid")

    risk_cap = payload.get("risk_cap")
    if risk_cap not in RISK_CAPS:
        raise ValueError("invalid risk_cap")

    rationale = payload.get("rationale")
    if not isinstance(rationale, list) or not all(isinstance(x, str) for x in rationale):
        raise ValueError("rationale invalid")


def validate_router_veto(payload: Any) -> None:
    """Validate router.veto payload."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol invalid")

    veto_reason = payload.get("veto_reason")
    if veto_reason not in VETO_REASONS:
        raise ValueError("invalid veto_reason")
