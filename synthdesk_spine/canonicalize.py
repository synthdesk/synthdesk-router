"""
Canonical float handling for synthdesk event emissions.

This module implements deterministic float canonicalization per spec_fp_determinism.md.
All floats must pass through canonicalization at the emission boundary to ensure:
- Identical logical values serialize identically
- Replay produces bit-identical JSON
- No NaN/Inf in emitted payloads

Status: NORMATIVE (LAW)
Version: FPDET-1
Registry Version: REG-1
"""

import math
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, Optional, Union


# ============================================================================
# Precision Registry (REG-1)
# ============================================================================
# Source: float_emission_inventory.md (2026-01-05)
#
# Format: {field_pattern: decimal_places}
# Special case: 0 dp = cast to int
# ============================================================================

PRECISION_REGISTRY = {
    # Category 1: Confidence Metrics (0.0 to 1.0)
    "confidence": 4,

    # Category 2: Correlation Coefficients (-1.0 to +1.0)
    "rolling_correlation": 4,
    "correlation": 4,

    # Category 3: Returns / Percentages
    "log_return": 6,
    "return_pct": 4,
    "deviation_pct": 4,
    "body_ratio": 4,

    # Category 4: Volatility Metrics
    "rolling_std": 6,
    "slope": 6,

    # Category 5: Z-Scores / Normalized Distances
    "zscore": 4,

    # Category 6: Thresholds / Config Values
    "breakout_threshold": 4,
    "threshold": 4,

    # Category 7: Prices / Absolute Values (USD cent precision)
    "price": 2,
    "open": 2,
    "high": 2,
    "low": 2,
    "close": 2,
    "rolling_mean": 2,
    "deviation": 2,
    "range": 2,
    "range_abs": 2,
    "body_abs": 2,

    # Category 8: Position Sizing (1 bps resolution)
    "size_pct": 4,

    # Category 9: Volumes (no fractional values)
    "volume": 0,
}

REGISTRY_VERSION = "REG-1"
FPDET_SPEC_VERSION = "FPDET-1"


# ============================================================================
# Canonicalization Functions
# ============================================================================

def canonicalize_float(value: float, decimal_places: int) -> Union[float, int, None]:
    """
    Canonicalize a float to fixed decimal places with explicit rounding.

    Rounding mode: ROUND_HALF_EVEN (banker's rounding)
    - Stable under Python's Decimal implementation
    - Industry standard for financial calculations

    Args:
        value: Raw float value
        decimal_places: Number of decimal places (0 = cast to int)

    Returns:
        - None if value is NaN or Inf (caller must handle as invariant violation)
        - int if decimal_places == 0
        - float otherwise, rounded to exact decimal places

    Examples:
        >>> canonicalize_float(0.123456, 4)
        0.1235
        >>> canonicalize_float(0.75, 0)
        0
        >>> canonicalize_float(float('nan'), 4)
        None
    """
    # NaN/Inf policy: Return None (caller must emit invariant violation)
    if not math.isfinite(value):
        return None

    # Special case: 0 decimal places = integer
    if decimal_places == 0:
        return int(Decimal(str(value)).quantize(
            Decimal('1'),
            rounding=ROUND_HALF_EVEN
        ))

    # Quantize using Decimal for deterministic rounding
    quantize_pattern = Decimal('0.1') ** decimal_places
    result = Decimal(str(value)).quantize(
        quantize_pattern,
        rounding=ROUND_HALF_EVEN
    )

    return float(result)


def canonicalize_field(field_name: str, value: Any) -> Union[float, int, None]:
    """
    Canonicalize a single field by name using the precision registry.

    Args:
        field_name: Name of the field (must exist in PRECISION_REGISTRY)
        value: Raw value (must be numeric)

    Returns:
        Canonicalized value (float, int, or None for NaN/Inf)

    Raises:
        ValueError: If field_name not in registry or value is non-numeric
    """
    if field_name not in PRECISION_REGISTRY:
        raise ValueError(
            f"Field '{field_name}' not in precision registry (REG-1). "
            f"Update PRECISION_REGISTRY or use explicit decimal_places."
        )

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(
            f"Field '{field_name}' has non-numeric value: {value} ({type(value)})"
        )

    decimal_places = PRECISION_REGISTRY[field_name]
    return canonicalize_float(float(value), decimal_places)


def canonicalize_payload(
    payload: Dict[str, Any],
    strict: bool = False,
    skip_unknown: bool = True,
) -> Dict[str, Any]:
    """
    Canonicalize all numeric fields in a payload dict.

    This function walks through a payload and canonicalizes any field that:
    1. Exists in PRECISION_REGISTRY
    2. Has a numeric value (int/float, not bool)

    NaN/Inf values are replaced with None (caller must handle as invariant violation).

    Args:
        payload: Event payload dict (may contain nested dicts)
        strict: If True, raise on unknown numeric fields. If False, pass through.
        skip_unknown: If True, skip fields not in registry. If False, raise.

    Returns:
        New dict with canonicalized numeric values

    Raises:
        ValueError: If strict=True and unknown numeric field encountered
    """
    result = {}

    for key, value in payload.items():
        # Recursively canonicalize nested dicts
        if isinstance(value, dict):
            result[key] = canonicalize_payload(value, strict=strict, skip_unknown=skip_unknown)
            continue

        # Skip non-numeric values
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            result[key] = value
            continue

        # Check if field is in registry
        if key not in PRECISION_REGISTRY:
            if strict and not skip_unknown:
                raise ValueError(
                    f"Numeric field '{key}' not in precision registry (REG-1). "
                    f"Add to PRECISION_REGISTRY or set skip_unknown=True."
                )
            # Pass through unknown fields unchanged if not strict
            result[key] = value
            continue

        # Canonicalize using registry
        result[key] = canonicalize_field(key, value)

    return result


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_finite(field_name: str, value: Union[float, int, None]) -> None:
    """
    Validate that a value is finite (not NaN/Inf).

    Raises:
        ValueError: If value is NaN or Inf
    """
    if value is None:
        raise ValueError(f"Field '{field_name}' is None after canonicalization (was NaN/Inf)")

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Field '{field_name}' is not finite: {value}")


def get_field_precision(field_name: str) -> Optional[int]:
    """
    Get the precision (decimal places) for a field name.

    Returns:
        Decimal places if field is in registry, None otherwise
    """
    return PRECISION_REGISTRY.get(field_name)


# ============================================================================
# Registry Metadata
# ============================================================================

def get_registry_info() -> Dict[str, Any]:
    """
    Get metadata about the current precision registry.

    Returns:
        Dict with registry version, spec version, and field count
    """
    return {
        "registry_version": REGISTRY_VERSION,
        "fpdet_spec_version": FPDET_SPEC_VERSION,
        "field_count": len(PRECISION_REGISTRY),
        "fields": list(PRECISION_REGISTRY.keys()),
    }
