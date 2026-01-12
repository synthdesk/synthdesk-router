"""
Envelope Provider - Bootstrap Monte Carlo Kernel (v0.2)

Computes probability envelopes from realized microstructure data.
This is the first "real" kernel - deterministic, auditable, no model-fitting.

Method: Historical bootstrapped returns
- Take last N returns from tick window
- Sample M paths by bootstrapping (with replacement)
- Compute distribution of terminal return at each horizon
- Map to probabilities

Determinism: Seeded RNG from hash(symbol, source_event_id, kernel_build_sha)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

# Kernel identity (frozen per release)
KERNEL_VERSION = "bootstrap_mc_v0.2"
KERNEL_BUILD_SHA = "bootstrap_mc_v0.2.1_20260112"  # Update on code changes (v0.2.1: asof_ts fix)


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

    p_long: float       # P(R > long_threshold)
    p_short: float      # P(R < short_threshold)
    p_flat: float       # 1 - p_long - p_short
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
        # Insufficient data: veto
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

    # Convert log returns to simple returns for probability calculation
    # exp(log_return) - 1 ≈ simple_return for small returns
    simple_terminal = np.expm1(terminal_returns)

    # Compute probabilities
    p_long = float(np.mean(simple_terminal > params.long_threshold))
    p_short = float(np.mean(simple_terminal < params.short_threshold))
    p_flat = 1.0 - p_long - p_short

    # Ensure non-negative
    p_flat = max(0.0, p_flat)

    # Risk metrics
    expected_return = float(np.mean(simple_terminal))
    return_std = float(np.std(simple_terminal))

    # Risk p95: 95th percentile of LOSS (negative returns)
    # Higher is worse (more potential loss)
    losses = -simple_terminal  # Negate so positive = loss
    risk_p95 = float(np.percentile(losses, params.risk_quantile * 100))

    # Veto heuristic: high uncertainty or extreme tail risk
    # p_vetoed indicates confidence in veto
    p_vetoed = 0.0

    # High uncertainty: both directions equally likely
    if abs(p_long - p_short) < 0.1 and max(p_long, p_short) > 0.4:
        p_vetoed = 0.3  # Moderate veto signal

    # Extreme tail risk
    if risk_p95 > 0.02:  # >2% potential loss at p95
        p_vetoed = max(p_vetoed, 0.5)

    return Envelope(
        p_long=p_long,
        p_short=p_short,
        p_flat=p_flat,
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
