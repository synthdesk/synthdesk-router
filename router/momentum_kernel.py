"""
Momentum Kernel v0.2 - Directional Signal from Regime Drift

EPISTEMIC CONTRACT:
- This kernel IS DIRECTIONAL. It claims direction when drift is detected.
- Direction comes from sign(z_mean), confidence from volatility-normalized drift.
- Only claims direction in "goldilocks zone": |z_mean| in [Z_DRIFT_MIN, Z_DRIFT_MAX]
- Above Z_DRIFT_MAX: mean reversion dominates, no directional claim
- Must pass EVT-0D before deployment (hit_rate >= 50%, calibration <= 0.15).

Legacy calibration notes (v0.1) are retained for provenance but are not used
by the current scoring function.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Kernel identity
KERNEL_NAME = "momentum_v0"
KERNEL_VERSION = "momentum_v0.2"
KERNEL_BUILD_SHA = "momentum_v0.2_20260120_volnorm"

# Epistemic contract
KERNEL_DIRECTIONAL = True  # This kernel DOES claim directional signal

# Regime gate: only claim direction for these regimes
DIRECTIONAL_REGIMES = {"drift", "breakout"}

# Calibration from dev split (Jan 14 2026, v0.2.2 data, 60min horizon, n=74)
# Key finding: momentum works in "goldilocks zone", extreme signals mean-revert
# Optimal range: |z_mean| in [0.00002, 0.00003)
Z_DRIFT_MIN = 0.00002     # Below this: regime classifier threshold, weak signal
Z_DRIFT_MAX = 0.00003     # Above this: mean reversion dominates (hit rate <50%)
SCORE_CAP = 1.0           # Volatility-normalized score at which p_dir caps
SCORE_EPS = 1e-12         # Avoid division by zero for z_std

# Analysis-only debug logging (opt-in via env var)
_DEBUG_ENV_VAR = "SYNTHDESK_MOMENTUM_DEBUG"


def _debug_enabled() -> bool:
    value = os.getenv(_DEBUG_ENV_VAR, "")
    return value.lower() in ("1", "true", "yes", "on")


def _emit_debug(payload: Dict[str, object]) -> None:
    if not _debug_enabled():
        return
    payload["debug_version"] = "momentum_v0_debug_v1"
    try:
        print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
    except Exception:
        # Debug logging must never affect behavior
        return


@dataclass(frozen=True)
class MomentumParams:
    """Parameters for momentum kernel."""

    z_drift_min: float = Z_DRIFT_MIN  # Minimum |z_mean| to claim direction
    z_drift_max: float = Z_DRIFT_MAX  # Maximum |z_mean| - above this, no claim
    max_confidence: float = 0.90      # Upper bound (no calibration)
    min_confidence: float = 0.55      # Lower bound for directional claims


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
# Legacy calibration map (v0.1, retained for provenance; unused by current scoring)

CALIBRATION_MAP: List[Tuple[float, float]] = [
    # (|z_mean| threshold, calibration bin)
    # Conservative: use lower bound of observed hit rates
    (0.00002, 0.75),   # 80% observed, round down for safety
    (0.000023, 0.85),  # 93% observed, cap at max_confidence
    (0.000025, 0.85),  # 93% observed
    (0.000028, 0.0),   # 40% observed -> NO CLAIM (below 50%)
    (0.00003, 0.0),    # Mean reversion zone
]


def _vol_norm_score(abs_z_mean: float, z_std: float) -> float:
    """Compute volatility-normalized drift score."""
    denom = z_std if z_std > SCORE_EPS else SCORE_EPS
    return abs_z_mean / denom


def _lookup_confidence(abs_z_mean: float, z_std: float, params: MomentumParams) -> float:
    """
    Compute confidence from volatility-normalized drift.

    Returns 0 if outside the valid momentum zone (mean reversion dominates).
    """
    # Gate: below minimum threshold
    if abs_z_mean < params.z_drift_min:
        return 0.0

    # Gate: above maximum threshold (mean reversion zone)
    if abs_z_mean >= params.z_drift_max:
        return 0.0

    # Inside goldilocks zone: scale p_dir by vol-normalized score
    score = _vol_norm_score(abs_z_mean, z_std)
    score_norm = min(score / SCORE_CAP, 1.0)
    return params.min_confidence + (params.max_confidence - params.min_confidence) * score_norm


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
    regime_confidence: Optional[float] = None,
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

    in_directional_regime = regime_val.lower() in DIRECTIONAL_REGIMES
    abs_z_mean = abs(z_mean_val)
    confidence = _lookup_confidence(abs_z_mean, z_std_val, params)

    # Gate 1: Regime must be directional
    if not in_directional_regime:
        _emit_debug({
            "symbol": symbol,
            "regime": regime_val,
            "regime_confidence": regime_confidence,
            "z_mean": z_mean_val,
            "z_std": z_std_val,
            "abs_z_mean": abs_z_mean,
            "vol_norm_score": _vol_norm_score(abs_z_mean, z_std_val),
            "score_cap": SCORE_CAP,
            "z_drift_min": params.z_drift_min,
            "z_drift_max": params.z_drift_max,
            "max_confidence": params.max_confidence,
            "min_confidence": params.min_confidence,
            "p_vetoed": p_vetoed,
            "in_directional_regime": in_directional_regime,
            "in_goldilocks": False,
            "confidence": confidence,
            "decision": "non_directional",
            "reason": "regime_gate",
        })
        return _non_directional()

    # Gate 2: |z_mean| must be in goldilocks zone [min, max)
    if confidence == 0.0:
        # Outside valid zone (too weak OR mean reversion zone)
        _emit_debug({
            "symbol": symbol,
            "regime": regime_val,
            "regime_confidence": regime_confidence,
            "z_mean": z_mean_val,
            "z_std": z_std_val,
            "abs_z_mean": abs_z_mean,
            "vol_norm_score": _vol_norm_score(abs_z_mean, z_std_val),
            "score_cap": SCORE_CAP,
            "z_drift_min": params.z_drift_min,
            "z_drift_max": params.z_drift_max,
            "max_confidence": params.max_confidence,
            "min_confidence": params.min_confidence,
            "p_vetoed": p_vetoed,
            "in_directional_regime": in_directional_regime,
            "in_goldilocks": False,
            "confidence": confidence,
            "decision": "non_directional",
            "reason": "z_mean_gate",
        })
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
    pre_scale = {"p_long": p_long, "p_short": p_short}
    scale = 1.0 - p_vetoed
    if p_vetoed > 0:
        p_long *= scale
        p_short *= scale

    _emit_debug({
        "symbol": symbol,
        "regime": regime_val,
        "regime_confidence": regime_confidence,
        "z_mean": z_mean_val,
        "z_std": z_std_val,
        "abs_z_mean": abs_z_mean,
        "vol_norm_score": _vol_norm_score(abs_z_mean, z_std_val),
        "score_cap": SCORE_CAP,
        "z_drift_min": params.z_drift_min,
        "z_drift_max": params.z_drift_max,
        "max_confidence": params.max_confidence,
        "min_confidence": params.min_confidence,
        "p_vetoed": p_vetoed,
        "scale": scale,
        "pre_scale": pre_scale,
        "post_scale": {"p_long": p_long, "p_short": p_short},
        "in_directional_regime": in_directional_regime,
        "in_goldilocks": True,
        "confidence": confidence,
        "decision": "directional",
        "reason": "goldilocks",
    })

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
