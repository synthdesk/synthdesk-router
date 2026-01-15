"""
Momentum 5m Kernel - Short-term directional signal.

HORIZON: 5 minutes (FROZEN - do not change)

EVT-0D Evidence (2026-01-15):
- 5m hit rate: 54.8% (+4.8% vs random)
- 15m hit rate: 44.1% (FAILS - do not use)
- Signal decays and inverts after 5m

This kernel is ONLY valid for 5-minute forward predictions.
Do not stretch to other horizons.

Calibration: Pending recalibration for 5m horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .base import KernelEnvelope, KernelInterface

# Kernel identity (frozen)
KERNEL_NAME = "momentum_5m"
KERNEL_VERSION = "0.1.0"
KERNEL_BUILD_SHA = "momentum_5m_v0.1.0_20260115_verified"

# Horizon binding (FROZEN - constitutional)
HORIZON_MINUTES = 5

# Epistemic contract
KERNEL_DIRECTIONAL = True

# Regime gate
DIRECTIONAL_REGIMES = {"drift", "breakout"}

# Calibration thresholds (TO BE RECALIBRATED for 5m)
# Current values inherited from failed 60m calibration
# These MUST be updated based on 5m EVT-0
Z_DRIFT_MIN = 0.00002
Z_DRIFT_MAX = 0.00003

# Confidence (TO BE RECALIBRATED)
# Current 90% is WRONG - placeholder until 5m calibration
DEFAULT_CONFIDENCE = 0.55  # Conservative: slightly above random


@dataclass(frozen=True)
class Momentum5mParams:
    """Parameters for 5m momentum kernel."""

    z_drift_min: float = Z_DRIFT_MIN
    z_drift_max: float = Z_DRIFT_MAX
    confidence: float = DEFAULT_CONFIDENCE


class Momentum5mKernel:
    """
    5-minute momentum kernel.

    Implements KernelInterface.
    """

    horizon_minutes: int = HORIZON_MINUTES
    kernel_name: str = KERNEL_NAME
    kernel_version: str = KERNEL_VERSION
    kernel_build_sha: str = KERNEL_BUILD_SHA

    def __init__(self, params: Optional[Momentum5mParams] = None):
        self.params = params or Momentum5mParams()

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
        """Generate 5m directional envelope."""

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
            return _non_directional()

        # Directional claim
        confidence = self.params.confidence

        if z_mean_val > 0:
            p_long = confidence
            p_short = 1.0 - confidence
        else:
            p_long = 1.0 - confidence
            p_short = confidence

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
def make_momentum_5m_envelope(
    symbol: str,
    prices: List[float],
    z_mean: Optional[float],
    z_std: Optional[float],
    regime: Optional[str],
    source_event_id: str,
    p_vetoed: float = 0.0,
    params: Optional[Momentum5mParams] = None,
) -> KernelEnvelope:
    """Convenience function for 5m momentum envelope."""
    kernel = Momentum5mKernel(params)
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
    "Momentum5mKernel",
    "Momentum5mParams",
    "make_momentum_5m_envelope",
    "KERNEL_NAME",
    "KERNEL_VERSION",
    "KERNEL_BUILD_SHA",
    "HORIZON_MINUTES",
]
