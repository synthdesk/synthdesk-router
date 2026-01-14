"""
Momentum Kernel v0.1 - Directional Signal from Regime Drift

EPISTEMIC CONTRACT:
- This kernel IS DIRECTIONAL. It claims direction when drift is detected.
- Direction comes from sign(z_mean), confidence from calibrated |z_mean|.
- Only claims direction in "goldilocks zone": |z_mean| in [Z_DRIFT_MIN, Z_DRIFT_MAX]
- Above Z_DRIFT_MAX: mean reversion dominates, no directional claim
- Must pass EVT-0D before deployment (hit_rate >= 50%, calibration <= 0.15).

Calibration Source: v0.2.2 dev data, Jan 14 2026, 60min horizon
Key Finding: Non-monotonic calibration curve
  |z_mean| 0.00002-0.00003 -> ~85-93% hit rate (momentum works)
  |z_mean| 0.00003-0.00005 -> ~40% hit rate (transition zone)
  |z_mean| 0.00005+        -> ~7% hit rate (mean reversion dominates)

Why this can work where bootstrap_mc cannot:
- z_mean is computed from ORDERED returns (preserves serial correlation)
- Bootstrap MC shuffles returns (destroys serial correlation)
- Drift detection is a trend-following signal, not a price distribution
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Kernel identity
KERNEL_NAME = "momentum_v0"
KERNEL_VERSION = "momentum_v0.1"
KERNEL_BUILD_SHA = "momentum_v0.1.2_20260114_calibrated"  # Pass 2: flat 90% conf

# Epistemic contract
KERNEL_DIRECTIONAL = True  # This kernel DOES claim directional signal

# Regime gate: only claim direction for these regimes
DIRECTIONAL_REGIMES = {"drift", "breakout"}

# Calibration from dev split (Jan 14 2026, v0.2.2 data, 60min horizon, n=74)
# Key finding: momentum works in "goldilocks zone", extreme signals mean-revert
# Optimal range: |z_mean| in [0.00002, 0.00003)
Z_DRIFT_MIN = 0.00002     # Below this: regime classifier threshold, weak signal
Z_DRIFT_MAX = 0.00003     # Above this: mean reversion dominates (hit rate <50%)


@dataclass(frozen=True)
class MomentumParams:
    """Parameters for momentum kernel."""

    z_drift_min: float = Z_DRIFT_MIN  # Minimum |z_mean| to claim direction
    z_drift_max: float = Z_DRIFT_MAX  # Maximum |z_mean| - above this, no claim
    # EVT-0D calibration pass 2: observed 90% hit rate, use flat 0.90 confidence
    # Previous pass had miscalibrated confidence curve (claimed 82% -> hit 40%)
    max_confidence: float = 0.90      # Observed hit rate in goldilocks zone
    min_confidence: float = 0.90      # Flat confidence (calibrated to reality)


@dataclass(frozen=True)
class MomentumEnvelope:
    """Probability envelope from momentum kernel."""

    p_long: float       # P(positive return) if directional
    p_short: float      # P(negative return) if directional
    p_flat: float       # 1 - p_vetoed when non-directional
    p_vetoed: float     # Veto probability (from risk assessment)

    # Provenance
    kernel: str
    kernel_version: str
    kernel_build_sha: str
    z_mean: float       # The drift signal used
    z_std: float        # Volatility signal
    regime: str         # Regime at decision time

    # Epistemic contract
    directional: bool   # Does this envelope claim direction?

    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        return {
            "p_long": self.p_long,
            "p_short": self.p_short,
            "p_flat": self.p_flat,
            "p_vetoed": self.p_vetoed,
            "kernel": self.kernel,
            "kernel_version": self.kernel_version,
            "kernel_build_sha": self.kernel_build_sha,
            "z_mean": self.z_mean,
            "z_std": self.z_std,
            "regime": self.regime,
            "directional": self.directional,
        }


# =============================================================================
# Calibration Map (FROZEN - from dev split Jan 14 2026)
# =============================================================================
# Source: v0.2.2 spine + ticks, 60min horizon, 74 drift outcomes
# Non-monotonic pattern: higher |z_mean| -> LOWER hit rate (mean reversion)
#
# Bin data:
#   [0.00002, 0.000023): n=15, hit_rate=80%
#   [0.000023, 0.000025): n=15, hit_rate=93%
#   [0.000025, 0.000028): n=14, hit_rate=93%
#   [0.000028, 0.00005): n=15, hit_rate=40%  <- BELOW 50%
#   [0.00005, 0.00016): n=14, hit_rate=7%    <- STRONGLY BELOW 50%
#
# Strategy: Only claim direction in bins with hit_rate >= 60%
# Use linear interpolation within the zone, cap at observed rates

CALIBRATION_MAP: List[Tuple[float, float]] = [
    # (|z_mean| threshold, confidence)
    # Conservative: use lower bound of observed hit rates
    (0.00002, 0.75),   # 80% observed, round down for safety
    (0.000023, 0.85),  # 93% observed, cap at max_confidence
    (0.000025, 0.85),  # 93% observed
    (0.000028, 0.0),   # 40% observed -> NO CLAIM (below 50%)
    (0.00003, 0.0),    # Mean reversion zone
]


def _lookup_confidence(abs_z_mean: float, params: MomentumParams) -> float:
    """
    Look up calibrated confidence for |z_mean|.

    Returns 0 if outside the valid momentum zone (mean reversion dominates).

    CALIBRATION NOTE (v0.1.2):
    EVT-0D showed 90% hit rate across the goldilocks zone, so we use flat
    confidence = 0.90 for all |z_mean| in [min, max). The previous linear
    ramp was miscalibrated (claimed 82% -> hit only 40%).
    """
    # Gate: below minimum threshold
    if abs_z_mean < params.z_drift_min:
        return 0.0

    # Gate: above maximum threshold (mean reversion zone)
    if abs_z_mean >= params.z_drift_max:
        return 0.0

    # Inside goldilocks zone: use flat calibrated confidence
    # All positions in zone get the same confidence (observed 90% hit rate)
    return params.max_confidence


def _compute_params_hash(params: MomentumParams) -> str:
    """Compute deterministic hash of parameters."""
    param_dict = {
        "z_drift_min": params.z_drift_min,
        "z_drift_max": params.z_drift_max,
        "max_confidence": params.max_confidence,
        "min_confidence": params.min_confidence,
    }
    param_str = json.dumps(param_dict, sort_keys=True)
    return hashlib.sha256(param_str.encode()).hexdigest()[:16]


def make_momentum_envelope(
    symbol: str,
    z_mean: Optional[float],
    z_std: Optional[float],
    regime: Optional[str],
    p_vetoed: float = 0.0,
    params: Optional[MomentumParams] = None,
) -> MomentumEnvelope:
    """
    Compute directional probability envelope from regime drift signal.

    Args:
        symbol: Symbol identifier
        z_mean: Normalized drift from regime classifier (signed)
        z_std: Normalized volatility from regime classifier
        regime: Current regime label
        p_vetoed: Veto probability from risk assessment (e.g., bootstrap_mc)
        params: Kernel parameters

    Returns:
        MomentumEnvelope with directional claims if conditions met
    """
    if params is None:
        params = MomentumParams()

    # Default values for missing inputs
    z_mean_val = z_mean if z_mean is not None else 0.0
    z_std_val = z_std if z_std is not None else 0.0
    regime_val = regime if regime is not None else "unknown"

    # Non-directional envelope (used for all rejection cases)
    def _non_directional() -> MomentumEnvelope:
        return MomentumEnvelope(
            p_long=0.0,
            p_short=0.0,
            p_flat=1.0 - p_vetoed,
            p_vetoed=p_vetoed,
            kernel=KERNEL_NAME,
            kernel_version=KERNEL_VERSION,
            kernel_build_sha=KERNEL_BUILD_SHA,
            z_mean=z_mean_val,
            z_std=z_std_val,
            regime=regime_val,
            directional=False,
        )

    # Gate 1: Regime must be directional
    if regime_val.lower() not in DIRECTIONAL_REGIMES:
        return _non_directional()

    # Gate 2: |z_mean| must be in goldilocks zone [min, max)
    abs_z_mean = abs(z_mean_val)
    confidence = _lookup_confidence(abs_z_mean, params)

    if confidence == 0.0:
        # Outside valid zone (too weak OR mean reversion zone)
        return _non_directional()

    # Directional claim!
    if z_mean_val > 0:
        # Positive drift -> long signal
        p_long = confidence
        p_short = 1.0 - confidence
    else:
        # Negative drift -> short signal
        p_long = 1.0 - confidence
        p_short = confidence

    # Normalize with veto (reduce directional mass proportionally)
    if p_vetoed > 0:
        scale = 1.0 - p_vetoed
        p_long *= scale
        p_short *= scale

    return MomentumEnvelope(
        p_long=p_long,
        p_short=p_short,
        p_flat=0.0,  # When directional, no flat claim
        p_vetoed=p_vetoed,
        kernel=KERNEL_NAME,
        kernel_version=KERNEL_VERSION,
        kernel_build_sha=KERNEL_BUILD_SHA,
        z_mean=z_mean_val,
        z_std=z_std_val,
        regime=regime_val,
        directional=True,
    )


__all__ = [
    "MomentumParams",
    "MomentumEnvelope",
    "make_momentum_envelope",
    "KERNEL_NAME",
    "KERNEL_VERSION",
    "KERNEL_BUILD_SHA",
    "KERNEL_DIRECTIONAL",
    "DIRECTIONAL_REGIMES",
    "Z_DRIFT_MIN",
    "Z_DRIFT_MAX",
    "CALIBRATION_MAP",
]
