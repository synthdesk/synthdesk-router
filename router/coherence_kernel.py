"""
Coherence Kernel v0.1

Cross-asset return sign agreement at 4h horizon.

Lineage: coherence_4h_v0.1
Preregistration: research/courts/LINEAGE-PREREG-COHERENCE-4H-2026-01-20.md

This kernel emits COHERENT or INCOHERENT based on whether assets
are moving in the same direction over the trailing 4h window.

No direction claimed. No confidence. No tuning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Kernel build SHA for lineage tracking
_SOURCE = Path(__file__).read_text()
KERNEL_BUILD_SHA = hashlib.sha256(_SOURCE.encode()).hexdigest()[:16]

# Frozen parameters (per preregistration)
HORIZON_HOURS = 4
HORIZON_MINUTES = HORIZON_HOURS * 60

# Frozen asset universe (per preregistration)
ASSETS = frozenset(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])


class CoherenceState(Enum):
    """Binary coherence classification."""
    COHERENT = "COHERENT"
    INCOHERENT = "INCOHERENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class CoherenceResult:
    """Result of coherence evaluation."""
    state: CoherenceState
    agreement_count: int  # How many assets share majority sign (2, 3, or 4)
    total_assets: int     # Always 4 for this lineage
    asset_signs: Dict[str, int]  # +1 (up), -1 (down), 0 (flat)
    majority_sign: Optional[int]  # +1, -1, or None if tie
    eval_ts: datetime
    window_start: datetime
    window_end: datetime
    kernel_build_sha: str = KERNEL_BUILD_SHA
    lineage_id: str = "coherence_4h_v0.1"

    @property
    def coherence_score(self) -> float:
        """Fraction of assets agreeing with majority."""
        return self.agreement_count / self.total_assets if self.total_assets > 0 else 0.0


@dataclass
class PricePoint:
    """Price observation for an asset."""
    ts: datetime
    symbol: str
    price: float


def compute_return_sign(prices: List[PricePoint], symbol: str,
                        window_start: datetime, window_end: datetime) -> Optional[int]:
    """
    Compute signed return for an asset over a window.

    Returns:
        +1 if price went up
        -1 if price went down
        None if insufficient data or flat (exact zero)
    """
    # Filter to this symbol and window
    symbol_prices = [
        p for p in prices
        if p.symbol == symbol and window_start <= p.ts <= window_end
    ]

    if len(symbol_prices) < 2:
        return None

    # Sort by timestamp
    symbol_prices.sort(key=lambda p: p.ts)

    # Get first and last price in window
    first_price = symbol_prices[0].price
    last_price = symbol_prices[-1].price

    if first_price == 0:
        return None

    ret = (last_price - first_price) / first_price

    # Classify sign (flat only if exact zero)
    epsilon = 0.0
    if ret > epsilon:
        return 1
    if ret < -epsilon:
        return -1
    return None


def compute_coherence(
    prices: List[PricePoint],
    eval_ts: datetime,
) -> CoherenceResult:
    """
    Compute cross-asset coherence at a given evaluation timestamp.

    Looks back HORIZON_HOURS to compute signed returns for each asset,
    then determines if assets agree on direction.

    Args:
        prices: List of price observations (all assets)
        eval_ts: Timestamp to evaluate coherence at

    Returns:
        CoherenceResult with state and diagnostics
    """
    window_end = eval_ts
    window_start = eval_ts - timedelta(hours=HORIZON_HOURS)

    # Compute sign for each asset
    asset_signs: Dict[str, int] = {}
    missing_assets: List[str] = []

    for symbol in ASSETS:
        sign = compute_return_sign(prices, symbol, window_start, window_end)
        if sign is None:
            missing_assets.append(symbol)
        else:
            asset_signs[symbol] = sign

    # Check for insufficient data
    if len(asset_signs) < len(ASSETS):
        return CoherenceResult(
            state=CoherenceState.INSUFFICIENT_DATA,
            agreement_count=0,
            total_assets=len(ASSETS),
            asset_signs=asset_signs,
            majority_sign=None,
            eval_ts=eval_ts,
            window_start=window_start,
            window_end=window_end,
        )

    # Count signs (excluding zeros)
    up_count = sum(1 for s in asset_signs.values() if s == 1)
    down_count = sum(1 for s in asset_signs.values() if s == -1)
    flat_count = sum(1 for s in asset_signs.values() if s == 0)

    # Determine majority
    if up_count > down_count:
        majority_sign = 1
        agreement_count = up_count
    elif down_count > up_count:
        majority_sign = -1
        agreement_count = down_count
    else:
        # Tie (2-2 split, or flats involved)
        majority_sign = None
        agreement_count = max(up_count, down_count)

    # Classify coherence (per preregistration section 4.3)
    # 4/4 or 3/4 agree = COHERENT
    # 2/2 split = INCOHERENT
    coherence_score = agreement_count / len(ASSETS)

    if coherence_score >= 0.75:  # 3/4 or 4/4
        state = CoherenceState.COHERENT
    else:
        state = CoherenceState.INCOHERENT

    return CoherenceResult(
        state=state,
        agreement_count=agreement_count,
        total_assets=len(ASSETS),
        asset_signs=asset_signs,
        majority_sign=majority_sign,
        eval_ts=eval_ts,
        window_start=window_start,
        window_end=window_end,
    )


def coherence_result_to_event(result: CoherenceResult) -> Dict:
    """
    Convert CoherenceResult to spine event format.

    Event type: coherence.state
    """
    return {
        "event_type": "coherence.state",
        "timestamp": result.eval_ts.isoformat(),
        "payload": {
            "state": result.state.value,
            "coherence_score": result.coherence_score,
            "agreement_count": result.agreement_count,
            "total_assets": result.total_assets,
            "asset_signs": result.asset_signs,
            "majority_sign": result.majority_sign,
            "window_start": result.window_start.isoformat(),
            "window_end": result.window_end.isoformat(),
            "horizon_hours": HORIZON_HOURS,
            "lineage_id": result.lineage_id,
            "kernel_build_sha": result.kernel_build_sha,
        },
    }
