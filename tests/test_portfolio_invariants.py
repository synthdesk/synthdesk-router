"""
Portfolio runtime invariants.

Focuses on integration-safe guarantees for sizing adjustments.
"""

from typing import List, Optional

from router.allocator import AllocationResult, Direction, RiskCap, SIZE_PCT_SCALE
from router.main import _apply_portfolio_allocation
from router.portfolio import portfolio_allocate


def _alloc(
    *,
    size_pct_q: int,
    direction: Direction = Direction.LONG,
    rationale: Optional[List[str]] = None,
) -> AllocationResult:
    return AllocationResult(
        direction=direction,
        size_pct_q=size_pct_q,
        size_pct_scale=SIZE_PCT_SCALE,
        risk_cap=RiskCap.LOW,
        rationale=rationale or ["test"],
        base_allocation_q=size_pct_q,
        entropy_factor=0.7,
        uncertainty_discount=1.0,
        final_factor=1.0,
    )


def test_portfolio_allocate_preserves_direction_and_caps_total_exposure() -> None:
    allocations = {
        "BTCUSDT": _alloc(size_pct_q=6000),
        "ETHUSDT": _alloc(size_pct_q=4500),
        "SOLUSDT": _alloc(size_pct_q=3000),
    }
    corr_matrix = {
        ("BTCUSDT", "ETHUSDT"): 0.95,
        ("BTCUSDT", "SOLUSDT"): 0.80,
    }

    result = portfolio_allocate(
        allocations,
        corr_matrix,
        anchor_symbol="BTCUSDT",
        max_total_exposure_q=10000,
    )

    adjusted_sum = sum(item.adjusted_size_q for item in result.allocations.values())
    assert adjusted_sum <= 10000

    for symbol, original in allocations.items():
        adjusted = result.allocations[symbol]
        assert adjusted.direction == original.direction
        assert 0 < adjusted.adjusted_size_q <= original.size_pct_q

    assert result.allocations["ETHUSDT"].adjusted_size_q < allocations["ETHUSDT"].size_pct_q
    assert result.allocations["SOLUSDT"].adjusted_size_q < allocations["SOLUSDT"].size_pct_q


def test_runtime_clamps_rounded_zero_only_for_positive_original(monkeypatch) -> None:
    monkeypatch.setenv("ROUTER_PORTFOLIO_ENABLE", "1")

    per_symbol = {
        "BTCUSDT": (_alloc(size_pct_q=5000, rationale=["orig_btc"]), None, None),
        # High-correlation penalty rounds this to zero before runtime clamp.
        "ETHUSDT": (_alloc(size_pct_q=1, rationale=["orig_eth"]), None, None),
        # Not eligible for portfolio pass (flat/zero) and must not be clamped.
        "XRPUSDT": (_alloc(size_pct_q=0, direction=Direction.FLAT, rationale=["orig_xrp"]), None, None),
    }
    state_dict = {
        "system": {"listener_alive": True},
        "symbols": {
            "ETHUSDT": {"rolling_correlation": 0.95},
        },
        "degraded_symbols": set(),
    }

    original = {symbol: tup[0] for symbol, tup in per_symbol.items()}
    _apply_portfolio_allocation(state_dict, per_symbol)

    btc = per_symbol["BTCUSDT"][0]
    eth = per_symbol["ETHUSDT"][0]
    xrp = per_symbol["XRPUSDT"][0]

    assert btc.direction == original["BTCUSDT"].direction
    assert eth.direction == original["ETHUSDT"].direction
    assert xrp.direction == original["XRPUSDT"].direction

    assert 0 < eth.size_pct_q <= original["ETHUSDT"].size_pct_q
    assert "portfolio:zero_clamped_to_1" in eth.rationale

    assert xrp.size_pct_q == 0
    assert all("zero_clamped_to_1" not in token for token in xrp.rationale)

    assert btc.size_pct_q + eth.size_pct_q <= 10000
