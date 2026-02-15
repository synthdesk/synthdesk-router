"""
Flow Pressure v0 — per-trade descriptive measurement of aggressive trade hostility.

DOCTRINE: FLOW_PRESSURE_V0
STATUS: FROZEN (2026-01-30) — BTC-density-calibrated
DO NOT TUNE THRESHOLDS. New thresholds require v1.
See docs/DOCTRINE_FLOW_PRESSURE_V0.md for calibration record.

Maintains a rolling deque of recent trades per symbol. On each new trade,
computes window stats (100ms, 500ms, 1000ms) and flags (burst, sweep, whale).

Rolling quantile estimator for whale thresholds (P2 algorithm approximation
via sorted reservoir). No lookahead: whale thresholds are strictly rolling.

Invariants:
  - source_ts from trade record used when present; fallback labeled
  - No lookahead in any computation
  - Monotonic output: ts_out_of_order flagged, never crashes
  - Append-only artifact output
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


@dataclass
class Trade:
    source_ts_ms: float  # milliseconds since epoch
    price: float
    qty: float
    side: int  # +1 buy aggressor, -1 sell aggressor
    ts_is_fallback: bool = False


@dataclass
class WindowStats:
    n: int = 0
    signed_n: int = 0
    vol: float = 0.0
    signed_vol: float = 0.0
    distinct_prices: int = 0


@dataclass
class Flags:
    burst_20in1000ms: bool = False
    burst_10in500ms: bool = False
    sweep_2px_in500ms: bool = False
    whale_p95: bool = False
    whale_p99: bool = False


class RollingQuantile:
    """Simple sorted-reservoir quantile estimator.

    Maintains a fixed-size sorted list of recent observations.
    Not the P2 algorithm — just a reservoir with periodic trim.
    Good enough for descriptive thresholds; no lookahead.
    """

    def __init__(self, capacity: int = 5000, warmup: int = 200):
        self._capacity = capacity
        self._warmup = warmup
        self._buf: List[float] = []
        self._sorted = False
        self._count = 0

    @property
    def warm(self) -> bool:
        return self._count >= self._warmup

    def add(self, val: float) -> None:
        self._count += 1
        self._buf.append(val)
        self._sorted = False
        if len(self._buf) > self._capacity * 2:
            self._buf.sort()
            self._buf = self._buf[-self._capacity:]
            self._sorted = True

    def quantile(self, q: float) -> Optional[float]:
        if not self.warm:
            return None
        if not self._sorted:
            self._buf.sort()
            self._sorted = True
        idx = int(q * (len(self._buf) - 1))
        return self._buf[idx]


class FlowPressureV0:
    """Core computation engine. Stateful per symbol."""

    def __init__(self):
        self._deques: Dict[str, Deque[Trade]] = {}
        self._qty_estimators: Dict[str, RollingQuantile] = {}
        self._last_ts: Dict[str, float] = {}

    def _ensure_symbol(self, symbol: str) -> None:
        if symbol not in self._deques:
            self._deques[symbol] = deque()
            self._qty_estimators[symbol] = RollingQuantile()
            self._last_ts[symbol] = 0.0

    def process_trade(
        self,
        symbol: str,
        source_ts_ms: float,
        price: float,
        qty: float,
        side: int,
        ts_is_fallback: bool = False,
    ) -> Dict[str, Any]:
        """Process one trade, return the flow_pressure event payload."""
        self._ensure_symbol(symbol)

        trade = Trade(
            source_ts_ms=source_ts_ms,
            price=price,
            qty=qty,
            side=side,
            ts_is_fallback=ts_is_fallback,
        )

        dq = self._deques[symbol]
        estimator = self._qty_estimators[symbol]

        # Detect out-of-order
        ts_out_of_order = source_ts_ms < self._last_ts[symbol]
        self._last_ts[symbol] = max(self._last_ts[symbol], source_ts_ms)

        # Add trade to deque and estimator
        dq.append(trade)
        estimator.add(qty)

        # Evict anything older than 1000ms
        cutoff_1000 = source_ts_ms - 1000.0
        while dq and dq[0].source_ts_ms < cutoff_1000:
            dq.popleft()

        # Compute window stats in a single pass
        w100 = WindowStats()
        w500 = WindowStats()
        w1000 = WindowStats()

        cutoff_500 = source_ts_ms - 500.0
        cutoff_100 = source_ts_ms - 100.0

        prices_500: set = set()
        prices_1000: set = set()
        prices_100: set = set()

        for t in dq:
            # 1000ms window (everything in deque qualifies)
            w1000.n += 1
            w1000.signed_n += t.side
            w1000.vol += t.qty
            w1000.signed_vol += t.qty * t.side
            prices_1000.add(t.price)

            if t.source_ts_ms >= cutoff_500:
                w500.n += 1
                w500.signed_n += t.side
                w500.vol += t.qty
                w500.signed_vol += t.qty * t.side
                prices_500.add(t.price)

            if t.source_ts_ms >= cutoff_100:
                w100.n += 1
                w100.signed_n += t.side
                w100.vol += t.qty
                w100.signed_vol += t.qty * t.side
                prices_100.add(t.price)

        w100.distinct_prices = len(prices_100)
        w500.distinct_prices = len(prices_500)
        w1000.distinct_prices = len(prices_1000)

        # Flags — calibrated against actual BTCUSDT tape density
        # n_1s: p50=54, p90=227, p95=307; n_500: p50=34, p90=159, p95=233
        # distinct_prices_500: p50=16, p90=83, p95=139
        # dominance: median ~0.89, so dominance alone doesn't discriminate
        # Strategy: high activity (p90+ count) AND directional dominance
        flags = Flags()
        # burst_1s: trade count >= p90 (~200) AND 80%+ same-direction
        flags.burst_20in1000ms = (
            w1000.n >= 200
            and w1000.n > 0
            and abs(w1000.signed_n) / w1000.n >= 0.80
        )
        # burst_500ms: trade count >= p90 (~150) AND 80%+ same-direction
        flags.burst_10in500ms = (
            w500.n >= 150
            and w500.n > 0
            and abs(w500.signed_n) / w500.n >= 0.80
        )
        # sweep: distinct prices >= p95 (~140) in 500ms — genuine book eating
        flags.sweep_2px_in500ms = w500.distinct_prices >= 140 and w500.n >= 50

        p95 = estimator.quantile(0.95)
        p99 = estimator.quantile(0.99)
        flags.whale_p95 = p95 is not None and qty >= p95
        flags.whale_p99 = p99 is not None and qty >= p99

        # Flow pressure score: simple z-ish composite
        # signed_vol_1s normalized by vol_1s + signed_n_1s normalized by n_1s
        score = 0.0
        if w1000.vol > 0:
            score += w1000.signed_vol / w1000.vol
        if w1000.n > 0:
            score += w1000.signed_n / w1000.n
        score = score / 2.0  # [-1, +1] range

        def ws_dict(ws: WindowStats) -> Dict[str, Any]:
            return {
                "n": ws.n,
                "signed_n": ws.signed_n,
                "vol": round(ws.vol, 8),
                "signed_vol": round(ws.signed_vol, 8),
                "distinct_prices": ws.distinct_prices,
            }

        return {
            "symbol": symbol,
            "source_ts_ms": source_ts_ms,
            "price": price,
            "qty": qty,
            "aggr_side": side,
            "ts_is_fallback": ts_is_fallback,
            "ts_out_of_order": ts_out_of_order,
            "w_100ms": ws_dict(w100),
            "w_500ms": ws_dict(w500),
            "w_1000ms": ws_dict(w1000),
            "flags": {
                "burst_20in1000ms": flags.burst_20in1000ms,
                "burst_10in500ms": flags.burst_10in500ms,
                "sweep_2px_in500ms": flags.sweep_2px_in500ms,
                "whale_p95": flags.whale_p95,
                "whale_p99": flags.whale_p99,
            },
            "flow_pressure_score": round(score, 6),
            "whale_thresholds": {
                "p95": round(p95, 8) if p95 is not None else None,
                "p99": round(p99, 8) if p99 is not None else None,
                "warm": estimator.warm,
            },
        }
