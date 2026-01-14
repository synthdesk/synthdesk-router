"""
Envelope Provider - Bootstrap Monte Carlo Kernel (v0.3)

Computes probability envelopes from realized microstructure data.
This is the first "real" kernel - deterministic, auditable, no model-fitting.

EPISTEMIC CONTRACT (v0.3):
- This kernel is NON-DIRECTIONAL. p_long and p_short are always 0.
- It provides RISK/VETO signals only: p_vetoed, risk_p95, return_std.
- Direction must come from a different kernel that passes EVT-0D.

Why non-directional:
- Bootstrap with replacement assumes i.i.d. returns (destroys serial correlation)
- Terminal threshold exceedance != directional probability
- EVT-0 showed 48% hit rate (noise) with flat calibration curve
- Honest about what it can't do; useful for what it can (risk detection)

Method: Historical bootstrapped returns
- Take last N returns from tick window
- Sample M paths by bootstrapping (with replacement)
- Compute distribution of terminal return at each horizon
- Use for volatility, tail risk, veto signals (NOT direction)

Determinism: Seeded RNG from hash(symbol, source_event_id, kernel_build_sha)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .momentum_kernel import (
    MomentumEnvelope,
    MomentumParams,
    make_momentum_envelope,
    KERNEL_NAME as MOMENTUM_KERNEL_NAME,
    KERNEL_VERSION as MOMENTUM_KERNEL_VERSION,
    KERNEL_BUILD_SHA as MOMENTUM_KERNEL_BUILD_SHA,
    KERNEL_DIRECTIONAL as MOMENTUM_KERNEL_DIRECTIONAL,
)

# Kernel identity (frozen per release)
KERNEL_VERSION = "bootstrap_mc_v0.3"
KERNEL_BUILD_SHA = "bootstrap_mc_v0.3.0_20260114"  # v0.3: non-directional, risk-only

# Composite kernel identity
COMPOSITE_KERNEL_VERSION = "composite_v0.1"
COMPOSITE_KERNEL_BUILD_SHA = "composite_v0.1.0_20260114"

# Epistemic contract flag
KERNEL_DIRECTIONAL = False  # This kernel does NOT claim directional signal


@dataclass(frozen=True)
class BootstrapParams:
    """Parameters for bootstrap MC simulation."""

    n_returns: int = 600      # Lookback: ~10 minutes at 1 tick/sec
    n_paths: int = 512        # Number of Monte Carlo paths
    horizon_steps: int = 60   # Forward steps to simulate (1 minute at 1 tick/sec)

    # Probability thresholds
    long_threshold: float = 0.001   # R > +0.1% to call "long"
    short_threshold: float = -0.001  # R < -0.1% to call "short"

    # Risk quantile
    risk_quantile: float = 0.95

    # Minimum returns required (below this, veto)
    min_returns: int = 100


@dataclass(frozen=True)
class Envelope:
    """Probability envelope from MC simulation."""

    p_long: float       # P(R > long_threshold) - always 0 for non-directional kernels
    p_short: float      # P(R < short_threshold) - always 0 for non-directional kernels
    p_flat: float       # 1 - p_vetoed for non-directional kernels
    p_vetoed: float     # Veto probability (tail risk / insufficient data)

    risk_p95: float     # 95th percentile of loss
    expected_return: float
    return_std: float

    # Provenance
    kernel: str
    kernel_version: str
    kernel_build_sha: str
    params_hash: str
    seed: int
    n_returns_used: int

    # Epistemic contract
    directional: bool = False  # Does this kernel claim directional signal?

    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        return {
            "p_long": self.p_long,
            "p_short": self.p_short,
            "p_flat": self.p_flat,
            "p_vetoed": self.p_vetoed,
            "risk_p95": self.risk_p95,
            "expected_return": self.expected_return,
            "return_std": self.return_std,
            "kernel": self.kernel,
            "kernel_version": self.kernel_version,
            "kernel_build_sha": self.kernel_build_sha,
            "params_hash": self.params_hash,
            "seed": self.seed,
            "n_returns_used": self.n_returns_used,
            "directional": self.directional,
        }


def _compute_params_hash(params: BootstrapParams) -> str:
    """Compute deterministic hash of parameters."""
    param_dict = {
        "n_returns": params.n_returns,
        "n_paths": params.n_paths,
        "horizon_steps": params.horizon_steps,
        "long_threshold": params.long_threshold,
        "short_threshold": params.short_threshold,
        "risk_quantile": params.risk_quantile,
        "min_returns": params.min_returns,
    }
    param_str = json.dumps(param_dict, sort_keys=True)
    return hashlib.sha256(param_str.encode()).hexdigest()[:16]


def _compute_seed(symbol: str, source_event_id: str, kernel_build_sha: str) -> int:
    """
    Compute deterministic seed for RNG.

    This ensures replay matches live exactly.
    """
    seed_str = f"{symbol}:{source_event_id}:{kernel_build_sha}"
    seed_hash = hashlib.sha256(seed_str.encode()).hexdigest()
    # Use first 8 hex chars as seed (32 bits)
    return int(seed_hash[:8], 16)


def _extract_returns(prices: List[float]) -> np.ndarray:
    """Extract log returns from price series."""
    if len(prices) < 2:
        return np.array([])
    prices_arr = np.array(prices)
    # Log returns for numerical stability
    returns = np.diff(np.log(prices_arr))
    return returns


def _bootstrap_paths(
    returns: np.ndarray,
    n_paths: int,
    horizon_steps: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Generate Monte Carlo paths by bootstrapping returns.

    Returns:
        Array of shape (n_paths, horizon_steps) with cumulative returns
    """
    n_returns = len(returns)

    # Sample return indices with replacement
    # Shape: (n_paths, horizon_steps)
    indices = rng.integers(0, n_returns, size=(n_paths, horizon_steps))

    # Get sampled returns
    sampled_returns = returns[indices]

    # Cumulative sum along time axis
    cumulative_returns = np.cumsum(sampled_returns, axis=1)

    return cumulative_returns


def make_envelope(
    symbol: str,
    prices: List[float],
    source_event_id: str,
    params: Optional[BootstrapParams] = None,
) -> Envelope:
    """
    Compute probability envelope from price history.

    This is the main interface for the router.

    Args:
        symbol: Symbol identifier (e.g., "BTCUSDT")
        prices: Recent price history (newest last)
        source_event_id: Event ID for deterministic seeding
        params: Bootstrap parameters (uses defaults if None)

    Returns:
        Envelope with probabilities and provenance
    """
    if params is None:
        params = BootstrapParams()

    params_hash = _compute_params_hash(params)
    seed = _compute_seed(symbol, source_event_id, KERNEL_BUILD_SHA)

    # Extract returns from price history
    returns = _extract_returns(prices)
    n_returns_available = len(returns)

    # Check minimum data requirement
    if n_returns_available < params.min_returns:
        # Insufficient data: full veto
        return Envelope(
            p_long=0.0,
            p_short=0.0,
            p_flat=0.0,
            p_vetoed=1.0,
            risk_p95=0.0,
            expected_return=0.0,
            return_std=0.0,
            kernel="bootstrap_mc",
            kernel_version=KERNEL_VERSION,
            kernel_build_sha=KERNEL_BUILD_SHA,
            params_hash=params_hash,
            seed=seed,
            n_returns_used=n_returns_available,
            directional=KERNEL_DIRECTIONAL,
        )

    # Use most recent N returns
    returns_window = returns[-params.n_returns:] if len(returns) > params.n_returns else returns
    n_returns_used = len(returns_window)

    # Create seeded RNG for determinism
    rng = np.random.default_rng(seed)

    # Generate Monte Carlo paths
    paths = _bootstrap_paths(
        returns_window,
        params.n_paths,
        params.horizon_steps,
        rng,
    )

    # Terminal returns (last column)
    terminal_returns = paths[:, -1]

    # Convert log returns to simple returns for risk calculation
    # exp(log_return) - 1 ≈ simple_return for small returns
    simple_terminal = np.expm1(terminal_returns)

    # Risk metrics (this is what bootstrap_mc is actually valid for)
    expected_return = float(np.mean(simple_terminal))
    return_std = float(np.std(simple_terminal))

    # Risk p95: 95th percentile of LOSS (negative returns)
    # Higher is worse (more potential loss)
    losses = -simple_terminal  # Negate so positive = loss
    risk_p95 = float(np.percentile(losses, params.risk_quantile * 100))

    # Veto heuristic: based on RISK, not direction
    # p_vetoed indicates tail risk / uncertainty
    p_vetoed = 0.0

    # High volatility regime
    if return_std > 0.005:  # >0.5% std over horizon
        p_vetoed = 0.3

    # Extreme tail risk
    if risk_p95 > 0.02:  # >2% potential loss at p95
        p_vetoed = max(p_vetoed, 0.5)

    # Very high tail risk
    if risk_p95 > 0.03:  # >3% potential loss at p95
        p_vetoed = max(p_vetoed, 0.7)

    # EPISTEMIC CONTRACT: No directional claims
    # p_long = p_short = 0; p_flat = 1 - p_vetoed
    # Bootstrap i.i.d. assumption destroys serial correlation,
    # making directional predictions no better than random (EVT-0 verified)

    return Envelope(
        p_long=0.0,         # NO DIRECTIONAL CLAIM
        p_short=0.0,        # NO DIRECTIONAL CLAIM
        p_flat=1.0 - p_vetoed,
        p_vetoed=p_vetoed,
        risk_p95=risk_p95,
        expected_return=expected_return,
        return_std=return_std,
        kernel="bootstrap_mc",
        kernel_version=KERNEL_VERSION,
        kernel_build_sha=KERNEL_BUILD_SHA,
        params_hash=params_hash,
        seed=seed,
        n_returns_used=n_returns_used,
        directional=KERNEL_DIRECTIONAL,
    )


# ---------------------------------------------------------------------
# Composite Envelope: momentum_v0 (direction) + bootstrap_mc (risk)
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class CompositeEnvelope:
    """
    Composite envelope combining multiple kernels.

    Direction from: momentum_v0 (EVT-0D validated)
    Risk from: bootstrap_mc v0.3 (EVT-0R validated)

    Policy: Only emit directional signal if momentum_v0 claims direction.
    Risk signal (p_vetoed) comes from bootstrap_mc regardless.
    """

    # Direction probabilities (from momentum_v0)
    p_long: float
    p_short: float
    p_flat: float

    # Risk/veto (from bootstrap_mc v0.3)
    p_vetoed: float
    risk_p95: float
    return_std: float

    # Epistemic contract
    directional: bool  # True only if momentum_v0 claims direction

    # Provenance: composite kernel
    kernel: str
    kernel_version: str
    kernel_build_sha: str

    # Provenance: direction source
    direction_kernel: str
    direction_kernel_version: str
    direction_kernel_build_sha: str

    # Provenance: risk source
    risk_kernel: str
    risk_kernel_version: str
    risk_kernel_build_sha: str

    # Input signals (for audit)
    z_mean: float
    z_std: float
    regime: str

    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        return {
            "p_long": self.p_long,
            "p_short": self.p_short,
            "p_flat": self.p_flat,
            "p_vetoed": self.p_vetoed,
            "risk_p95": self.risk_p95,
            "return_std": self.return_std,
            "directional": self.directional,
            "kernel": self.kernel,
            "kernel_version": self.kernel_version,
            "kernel_build_sha": self.kernel_build_sha,
            "direction_kernel": self.direction_kernel,
            "direction_kernel_version": self.direction_kernel_version,
            "direction_kernel_build_sha": self.direction_kernel_build_sha,
            "risk_kernel": self.risk_kernel,
            "risk_kernel_version": self.risk_kernel_version,
            "risk_kernel_build_sha": self.risk_kernel_build_sha,
            "z_mean": self.z_mean,
            "z_std": self.z_std,
            "regime": self.regime,
        }


def make_composite_envelope(
    symbol: str,
    prices: List[float],
    source_event_id: str,
    z_mean: Optional[float],
    z_std: Optional[float],
    regime: Optional[str],
    bootstrap_params: Optional[BootstrapParams] = None,
    momentum_params: Optional[MomentumParams] = None,
) -> CompositeEnvelope:
    """
    Create composite envelope combining direction and risk signals.

    Direction: momentum_v0 kernel (from z_mean drift signal)
    Risk: bootstrap_mc v0.3 kernel (from price volatility)

    The composite envelope:
    - p_long/p_short/p_flat: from momentum_v0 if directional, else 0/0/1
    - p_vetoed: from bootstrap_mc (risk signal)
    - risk_p95/return_std: from bootstrap_mc
    - directional: True only if momentum_v0 claims direction

    Args:
        symbol: Symbol identifier
        prices: Recent price history for bootstrap_mc
        source_event_id: Event ID for deterministic seeding
        z_mean: Normalized drift from regime classifier (for momentum_v0)
        z_std: Normalized volatility (for momentum_v0)
        regime: Current regime label (for momentum_v0)
        bootstrap_params: Parameters for bootstrap_mc
        momentum_params: Parameters for momentum_v0

    Returns:
        CompositeEnvelope with direction + risk signals
    """
    # Get risk signal from bootstrap_mc
    risk_envelope = make_envelope(
        symbol=symbol,
        prices=prices,
        source_event_id=source_event_id,
        params=bootstrap_params,
    )

    # Get direction signal from momentum_v0
    # Pass p_vetoed from bootstrap_mc to scale directional mass
    momentum_envelope = make_momentum_envelope(
        symbol=symbol,
        z_mean=z_mean,
        z_std=z_std,
        regime=regime,
        p_vetoed=risk_envelope.p_vetoed,
        params=momentum_params,
    )

    # Combine: direction from momentum, risk from bootstrap
    if momentum_envelope.directional:
        # momentum_v0 claims direction - use its probabilities
        # Note: momentum_envelope already scaled by p_vetoed
        p_long = momentum_envelope.p_long
        p_short = momentum_envelope.p_short
        p_flat = momentum_envelope.p_flat
        directional = True
    else:
        # No directional claim - stay flat
        p_long = 0.0
        p_short = 0.0
        p_flat = 1.0 - risk_envelope.p_vetoed
        directional = False

    return CompositeEnvelope(
        p_long=p_long,
        p_short=p_short,
        p_flat=p_flat,
        p_vetoed=risk_envelope.p_vetoed,
        risk_p95=risk_envelope.risk_p95,
        return_std=risk_envelope.return_std,
        directional=directional,
        kernel="composite",
        kernel_version=COMPOSITE_KERNEL_VERSION,
        kernel_build_sha=COMPOSITE_KERNEL_BUILD_SHA,
        direction_kernel=MOMENTUM_KERNEL_NAME,
        direction_kernel_version=MOMENTUM_KERNEL_VERSION,
        direction_kernel_build_sha=MOMENTUM_KERNEL_BUILD_SHA,
        risk_kernel="bootstrap_mc",
        risk_kernel_version=KERNEL_VERSION,
        risk_kernel_build_sha=KERNEL_BUILD_SHA,
        z_mean=z_mean if z_mean is not None else 0.0,
        z_std=z_std if z_std is not None else 0.0,
        regime=regime if regime is not None else "unknown",
    )


# ---------------------------------------------------------------------
# Tick buffer for runtime use
# ---------------------------------------------------------------------

@dataclass
class TimestampedTick:
    """Tick with timestamp for asof slicing."""
    ts_epoch: float  # Unix timestamp
    price: float


class TickBuffer:
    """
    Circular buffer for maintaining recent tick prices per symbol.

    CRITICAL: Stores timestamps to enforce tick_ts <= event_ts constraint.
    This prevents future data leakage when router replays historical events.

    The router uses this to feed the envelope provider without
    reading the full tick file on each event.
    """

    def __init__(self, max_size: int = 1200):
        """
        Initialize tick buffer.

        Args:
            max_size: Maximum ticks to retain per symbol
        """
        self._max_size = max_size
        self._buffers: Dict[str, List[TimestampedTick]] = {}

    def add_tick(self, symbol: str, ts_epoch: float, price: float) -> None:
        """
        Add a tick to the buffer.

        Args:
            symbol: Symbol identifier
            ts_epoch: Unix timestamp of the tick
            price: Tick price
        """
        if symbol not in self._buffers:
            self._buffers[symbol] = []

        buf = self._buffers[symbol]
        buf.append(TimestampedTick(ts_epoch=ts_epoch, price=price))

        # Trim if over capacity
        if len(buf) > self._max_size:
            # Keep most recent
            self._buffers[symbol] = buf[-self._max_size:]

    def get_prices(
        self,
        symbol: str,
        asof_ts: Optional[float] = None,
        n: Optional[int] = None,
    ) -> List[float]:
        """
        Get recent prices for symbol, respecting asof constraint.

        CRITICAL: If asof_ts is provided, only returns ticks where
        tick_ts <= asof_ts. This prevents future data leakage.

        Args:
            symbol: Symbol identifier
            asof_ts: Unix timestamp cutoff (only ticks at or before this time)
            n: Number of recent prices to return (None = all matching)

        Returns:
            List of prices (oldest first), filtered by asof_ts if provided
        """
        buf = self._buffers.get(symbol, [])

        if asof_ts is not None:
            # Filter to ticks at or before asof_ts
            # This is the critical data integrity gate
            filtered = [t.price for t in buf if t.ts_epoch <= asof_ts]
        else:
            filtered = [t.price for t in buf]

        if n is None or n >= len(filtered):
            return filtered
        return filtered[-n:]

    def get_prices_with_timestamps(
        self,
        symbol: str,
        asof_ts: Optional[float] = None,
    ) -> List[Tuple[float, float]]:
        """
        Get (ts_epoch, price) tuples for debugging/audit.

        Args:
            symbol: Symbol identifier
            asof_ts: Unix timestamp cutoff

        Returns:
            List of (ts_epoch, price) tuples
        """
        buf = self._buffers.get(symbol, [])

        if asof_ts is not None:
            return [(t.ts_epoch, t.price) for t in buf if t.ts_epoch <= asof_ts]
        return [(t.ts_epoch, t.price) for t in buf]

    def count(self, symbol: str, asof_ts: Optional[float] = None) -> int:
        """
        Get tick count for symbol.

        Args:
            symbol: Symbol identifier
            asof_ts: If provided, count only ticks at or before this time
        """
        buf = self._buffers.get(symbol, [])
        if asof_ts is not None:
            return sum(1 for t in buf if t.ts_epoch <= asof_ts)
        return len(buf)

    def clear(self, symbol: Optional[str] = None) -> None:
        """Clear buffer(s)."""
        if symbol is None:
            self._buffers.clear()
        elif symbol in self._buffers:
            del self._buffers[symbol]

    def oldest_tick_ts(self, symbol: str) -> Optional[float]:
        """Get timestamp of oldest tick for symbol (for debugging)."""
        buf = self._buffers.get(symbol, [])
        if buf:
            return buf[0].ts_epoch
        return None

    def newest_tick_ts(self, symbol: str) -> Optional[float]:
        """Get timestamp of newest tick for symbol (for debugging)."""
        buf = self._buffers.get(symbol, [])
        if buf:
            return buf[-1].ts_epoch
        return None
