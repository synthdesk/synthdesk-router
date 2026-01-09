"""Schema + validation for router.intent and router.veto."""

from __future__ import annotations

import math
from typing import Any, Tuple

# Intent fields
DIRECTIONS = {"long", "short"}  # No "flat" - flat is veto, not intent

# Risk caps by format (constitutional)
# v0.2 posture layer: conservative caps only (no execution authority)
RISK_CAPS_V02 = {"zero", "low", "medium"}
# Legacy format: included for backward compatibility
RISK_CAPS_LEGACY = {"low", "normal", "high"}
# Combined for validation (union)
RISK_CAPS = RISK_CAPS_V02 | RISK_CAPS_LEGACY

# Veto reasons (frozen, exhaustive)
# Constitutional: changes require governance amendment
VETO_REASONS = {
    "invariant_violation",
    "input_unavailable",
    "regime_unresolved",
    "authority_gate",  # v0.1 blocks non-flat posture (added v0.2)
}

# Quantization constants (v0.2)
SIZE_PCT_SCALE = 10000  # Denominator for quantized sizing


def validate_router_intent(payload: Any) -> Tuple[bool, str]:
    """
    Validate router.intent payload.

    Supports both legacy (size_pct float) and v0.2 (size_pct_q integer) formats.
    Mixed-format payloads are REJECTED (must be exactly one format).

    Args:
        payload: Intent payload dict

    Returns:
        (valid, error_message) - valid=True if OK, else error_message explains why

    Raises:
        ValueError: If payload is invalid (for backward compatibility)
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    direction = payload.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(f"invalid direction: {direction} (must be long/short, not flat)")

    # Detect format: v0.2 quantized vs legacy float
    has_size_pct_q = "size_pct_q" in payload
    has_size_pct_scale = "size_pct_scale" in payload
    has_size_pct = "size_pct" in payload

    # CRITICAL: Reject mixed-format payloads
    if has_size_pct and (has_size_pct_q or has_size_pct_scale):
        raise ValueError("mixed format: cannot have both size_pct and size_pct_q/size_pct_scale")

    if has_size_pct_q or has_size_pct_scale:
        # v0.2 quantized format - MUST have both fields
        if not has_size_pct_q:
            raise ValueError("v0.2 format requires size_pct_q")
        if not has_size_pct_scale:
            raise ValueError("v0.2 format requires size_pct_scale")

        size_pct_q = payload.get("size_pct_q")
        if not isinstance(size_pct_q, int) or size_pct_q < 0:
            raise ValueError("size_pct_q must be non-negative integer")

        size_pct_scale = payload.get("size_pct_scale")
        if size_pct_scale != SIZE_PCT_SCALE:
            raise ValueError(f"size_pct_scale must be {SIZE_PCT_SCALE}")

        # v0.2 format: enforce v0.2 risk caps
        risk_cap = payload.get("risk_cap")
        if risk_cap not in RISK_CAPS_V02:
            raise ValueError(f"v0.2 risk_cap must be one of {sorted(RISK_CAPS_V02)}, got: {risk_cap}")

        # v0.2: non-flat intent with zero size is invalid (should be veto)
        if size_pct_q == 0 and direction in DIRECTIONS:
            raise ValueError("v0.2: size_pct_q=0 with non-flat direction is invalid (should be veto)")

    else:
        # Legacy float format
        size_pct = payload.get("size_pct")
        if size_pct is None:
            raise ValueError("legacy format requires size_pct")
        if not isinstance(size_pct, (int, float)) or isinstance(size_pct, bool):
            raise ValueError("size_pct must be numeric")
        if not math.isfinite(size_pct) or size_pct < 0.0:
            raise ValueError("size_pct must be finite and non-negative")

        # Legacy format: enforce legacy risk caps
        risk_cap = payload.get("risk_cap")
        if risk_cap not in RISK_CAPS_LEGACY:
            raise ValueError(f"legacy risk_cap must be one of {sorted(RISK_CAPS_LEGACY)}, got: {risk_cap}")

    rationale = payload.get("rationale")
    if not isinstance(rationale, list) or not all(isinstance(x, str) for x in rationale):
        raise ValueError("rationale must be list of strings")
    if len(rationale) == 0:
        raise ValueError("rationale must not be empty")


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
