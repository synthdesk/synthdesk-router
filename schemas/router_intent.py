"""Schema + validation for router.intent."""

from __future__ import annotations

from typing import Any
import math

DIRECTIONS = {"long", "short", "flat"}
RISK_CAPS = {"low", "normal", "high"}


def validate_router_intent(payload: Any) -> None:
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
