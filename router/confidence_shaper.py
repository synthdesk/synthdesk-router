"""
Confidence Shaper (Phase 4)

Applies epistemic compression to MC envelope probabilities.
Maps model-confident p_dir to reality-calibrated confidence.

Transform: logit temperature shrink
    z = log(p / (1 - p))
    z' = z / T
    p' = sigmoid(z')

Properties:
- Monotonic: preserves ranking
- Symmetric around 0.5
- Deterministic
- Single parameter T (temperature)

When T > 1: compresses toward 0.5 (reduces overconfidence)
When T = 1: identity (no change)
When T < 1: amplifies away from 0.5 (increases confidence)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ---------------------------
# Frozen shaping parameters (Phase 4)
# ---------------------------

# Calibrated via deterministic grid search on frozen slice
# See docs/CONFIDENCE_SHAPER_SPEC_V1.md for provenance
CONF_SHAPER_VERSION = "v0.1.0"
CONF_SHAPER_T: float = 1.2  # Calibrated 2026-01-10

# Calibration slice provenance
CALIBRATION_SLICE_HASH: str = "spine_recalibrated_20260110"
CALIBRATION_DATE: str = "2026-01-10"

# Calibration method:
# - Grid search T in [1.0, 3.0] step 0.1
# - Objective: first T where max(p_long, p_short) < 0.70 after shaping
# - MC raw claims ~0.71, actual hit rate ~0.597
# - T=1.2 maps 0.71 → 0.678, bringing below overconfidence threshold
# - Calibration gap: |0.678 - 0.597| = 0.081 (within 0.15 limit)


@dataclass(frozen=True)
class ShapedConfidence:
    """Result of confidence shaping."""

    # Raw inputs
    p_long_raw: float
    p_short_raw: float
    p_flat_raw: float
    confidence_raw: float  # max(p_long, p_short)

    # Shaped outputs
    p_long_shaped: float
    p_short_shaped: float
    p_flat_shaped: float
    confidence_shaped: float  # max(p_long_shaped, p_short_shaped)

    # Shaping metadata
    temperature: float
    version: str


def _logit(p: float) -> float:
    """Logit function with clamping."""
    p = max(1e-6, min(1.0 - 1e-6, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    """Sigmoid function with overflow protection."""
    if x > 20:
        return 1.0
    if x < -20:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def shape_probability(p: float, temperature: float) -> float:
    """
    Apply logit temperature shrink to a single probability.

    Args:
        p: Probability in [0, 1]
        temperature: T > 1 compresses toward 0.5

    Returns:
        Shaped probability
    """
    if temperature <= 0:
        raise ValueError("Temperature must be positive")
    if temperature == 1.0:
        return p  # Identity

    z = _logit(p)
    z_shaped = z / temperature
    return _sigmoid(z_shaped)


def shape_direction_probs(
    p_flat: float,
    p_long: float,
    p_short: float,
    temperature: float,
) -> tuple[float, float, float]:
    """
    Shape direction probabilities while preserving normalization.

    We shape p_long and p_short individually, then renormalize
    to maintain p_flat + p_long + p_short = 1.0

    Args:
        p_flat, p_long, p_short: Raw direction probabilities
        temperature: Shaping temperature

    Returns:
        (p_flat_shaped, p_long_shaped, p_short_shaped)
    """
    if temperature == 1.0:
        return p_flat, p_long, p_short

    # Shape directional probs (not flat)
    p_long_s = shape_probability(p_long, temperature) if p_long > 0.01 else p_long
    p_short_s = shape_probability(p_short, temperature) if p_short > 0.01 else p_short

    # Renormalize: scale directional probs so total = 1.0
    # Keep relative balance between long/short, adjust flat
    directional_raw = p_long + p_short
    directional_shaped = p_long_s + p_short_s

    if directional_raw < 0.01:
        # Nearly all flat, don't touch
        return p_flat, p_long, p_short

    # Calculate shaped flat to maintain sum = 1.0
    p_flat_s = 1.0 - directional_shaped
    p_flat_s = max(0.0, min(1.0, p_flat_s))

    # Normalize if needed
    total = p_flat_s + p_long_s + p_short_s
    if abs(total - 1.0) > 1e-6:
        p_flat_s /= total
        p_long_s /= total
        p_short_s /= total

    return p_flat_s, p_long_s, p_short_s


def shape_confidence(
    p_flat: float,
    p_long: float,
    p_short: float,
    temperature: float,
) -> ShapedConfidence:
    """
    Full confidence shaping with provenance.

    Args:
        p_flat, p_long, p_short: Raw MC envelope probabilities
        temperature: Shaping temperature (T > 1 reduces overconfidence)

    Returns:
        ShapedConfidence with raw/shaped values and metadata
    """
    confidence_raw = max(p_long, p_short)

    p_flat_s, p_long_s, p_short_s = shape_direction_probs(
        p_flat, p_long, p_short, temperature
    )

    confidence_shaped = max(p_long_s, p_short_s)

    return ShapedConfidence(
        p_long_raw=p_long,
        p_short_raw=p_short,
        p_flat_raw=p_flat,
        confidence_raw=confidence_raw,
        p_long_shaped=p_long_s,
        p_short_shaped=p_short_s,
        p_flat_shaped=p_flat_s,
        confidence_shaped=confidence_shaped,
        temperature=temperature,
        version=CONF_SHAPER_VERSION,
    )


def get_calibrated_temperature() -> float:
    """
    Get the frozen calibrated temperature.
    """
    return CONF_SHAPER_T


__all__ = [
    "shape_probability",
    "shape_direction_probs",
    "shape_confidence",
    "get_calibrated_temperature",
    "ShapedConfidence",
    "CONF_SHAPER_VERSION",
    "CONF_SHAPER_T",
]
