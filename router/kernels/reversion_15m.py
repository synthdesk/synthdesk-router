"""
Reversion 15m Kernel - Mean reversion signal.

HORIZON: 15 minutes (FROZEN - do not change)

Design Rationale (2026-01-15):
EVT-0D showed momentum direction INVERTS at 15m:
- 5m momentum hit rate: 54.8% (momentum works)
- 15m momentum hit rate: 44.1% (momentum FAILS)

If momentum is WRONG 55.9% of the time at 15m,
then betting AGAINST momentum should hit ~55.9%.

This kernel inverts the z_mean signal:
- Positive z_mean (momentum up) → predict SHORT (reversion down)
- Negative z_mean (momentum down) → predict LONG (reversion up)

STATUS: FAILED - EVT-0 holdout validation failed
- Original slice: 55.0% hit rate (passed)
- Holdout slice: 41.3% hit rate (FAILED)
- The inversion hypothesis does not generalize
- See research/courts/REVERSION_15M_FAIL_20260115/

DO NOT USE IN PRODUCTION
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .base import KernelEnvelope, KernelInterface

# Kernel identity (frozen)
KERNEL_NAME = "reversion_15m"
KERNEL_VERSION = "0.1.0"
KERNEL_BUILD_SHA = "reversion_15m_v0.1.0_20260115_FAILED"

# Horizon binding (FROZEN - constitutional)
HORIZON_MINUTES = 15

# Epistemic contract
KERNEL_DIRECTIONAL = True

# Regime gate - reversion works in drift regimes
# (strong moves tend to reverse)
DIRECTIONAL_REGIMES = {"drift", "breakout"}

# Calibration thresholds (initial - TO BE VALIDATED)
# Signal must be strong enough to expect reversion
Z_DRIFT_MIN = 0.00002  # Minimum signal strength
Z_DRIFT_MAX = 0.00005  # Don't bet against very strong moves

# Confidence (initial estimate from EVT-0D)
# 15m momentum was 44.1%, so inversion should be ~55.9%
# Setting conservative 0.55 - MUST BE VALIDATED ON HOLDOUT
DEFAULT_CONFIDENCE = 0.55


@dataclass(frozen=True)
class Reversion15mParams:
    """Parameters for 15m reversion kernel."""

    z_drift_min: float = Z_DRIFT_MIN
    z_drift_max: float = Z_DRIFT_MAX
    confidence: float = DEFAULT_CONFIDENCE


class Reversion15mKernel:
    """
    15-minute mean reversion kernel.

    Implements KernelInterface.

    Key difference from momentum: INVERTS the z_mean signal.
    If z_mean > 0 (momentum up), predicts SHORT (reversion down).
    """

    horizon_minutes: int = HORIZON_MINUTES
    kernel_name: str = KERNEL_NAME
    kernel_version: str = KERNEL_VERSION
    kernel_build_sha: str = KERNEL_BUILD_SHA

    def __init__(self, params: Optional[Reversion15mParams] = None):
        self.params = params or Reversion15mParams()

    def make_envelope(
        self,
        symbol: str,
        prices: List[float],
        z_mean: Optional[float],
        z_std: Optional[float],
        regime: Optional[str],
        source_event_id: str,
        p_vetoed: float = 0.0,
    ) -> KernelEnvelope:
        """Generate 15m reversion envelope (INVERTS momentum direction)."""

        z_mean_val = z_mean if z_mean is not None else 0.0
        z_std_val = z_std if z_std is not None else 0.0
        regime_val = regime if regime is not None else "unknown"

        def _non_directional() -> KernelEnvelope:
            return KernelEnvelope(
                p_long=0.0,
                p_short=0.0,
                p_flat=1.0 - p_vetoed,
                p_vetoed=p_vetoed,
                directional=False,
                horizon_minutes=HORIZON_MINUTES,
                kernel_name=KERNEL_NAME,
                kernel_version=KERNEL_VERSION,
                kernel_build_sha=KERNEL_BUILD_SHA,
                z_mean=z_mean_val,
                z_std=z_std_val,
                regime=regime_val,
            )

        # Gate 1: Regime must be directional
        if regime_val.lower() not in DIRECTIONAL_REGIMES:
            return _non_directional()

        # Gate 2: |z_mean| must be in valid range
        abs_z_mean = abs(z_mean_val)
        if abs_z_mean < self.params.z_drift_min:
            return _non_directional()
        if abs_z_mean >= self.params.z_drift_max:
            # Very strong moves might continue - don't bet against
            return _non_directional()

        # Reversion claim - INVERT momentum direction
        confidence = self.params.confidence

        # KEY INVERSION: opposite of momentum kernel
        # If z_mean > 0 (momentum up), we predict SHORT (reversion down)
        # If z_mean < 0 (momentum down), we predict LONG (reversion up)
        if z_mean_val > 0:
            # Momentum is up → expect reversion down
            p_long = 1.0 - confidence
            p_short = confidence
        else:
            # Momentum is down → expect reversion up
            p_long = confidence
            p_short = 1.0 - confidence

        # Scale by veto
        if p_vetoed > 0:
            scale = 1.0 - p_vetoed
            p_long *= scale
            p_short *= scale

        return KernelEnvelope(
            p_long=p_long,
            p_short=p_short,
            p_flat=0.0,
            p_vetoed=p_vetoed,
            directional=True,
            horizon_minutes=HORIZON_MINUTES,
            kernel_name=KERNEL_NAME,
            kernel_version=KERNEL_VERSION,
            kernel_build_sha=KERNEL_BUILD_SHA,
            z_mean=z_mean_val,
            z_std=z_std_val,
            regime=regime_val,
        )


# Module-level factory
def make_reversion_15m_envelope(
    symbol: str,
    prices: List[float],
    z_mean: Optional[float],
    z_std: Optional[float],
    regime: Optional[str],
    source_event_id: str,
    p_vetoed: float = 0.0,
    params: Optional[Reversion15mParams] = None,
) -> KernelEnvelope:
    """Convenience function for 15m reversion envelope."""
    kernel = Reversion15mKernel(params)
    return kernel.make_envelope(
        symbol=symbol,
        prices=prices,
        z_mean=z_mean,
        z_std=z_std,
        regime=regime,
        source_event_id=source_event_id,
        p_vetoed=p_vetoed,
    )


__all__ = [
    "Reversion15mKernel",
    "Reversion15mParams",
    "make_reversion_15m_envelope",
    "KERNEL_NAME",
    "KERNEL_VERSION",
    "KERNEL_BUILD_SHA",
    "HORIZON_MINUTES",
]
