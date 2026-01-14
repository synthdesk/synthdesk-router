"""
Tests for Portfolio Allocator.

Validates correlation penalties, exposure caps, and portfolio-level allocation.
"""

import pytest
from router.portfolio import (
    CorrelationBucket,
    CORRELATION_HIGH_THRESHOLD,
    CORRELATION_LOW_THRESHOLD,
    CORRELATION_PENALTY_FACTORS,
    MAX_TOTAL_EXPOSURE_Q,
    PortfolioAllocation,
    SymbolAllocation,
    build_correlation_matrix_from_state,
    get_correlation_bucket,
    portfolio_allocate,
)
from router.allocator import AllocationResult, Direction, RiskCap, SIZE_PCT_SCALE


def make_allocation(
    direction: Direction = Direction.LONG,
    size_pct_q: int = 2500,
    risk_cap: RiskCap = RiskCap.LOW,
) -> AllocationResult:
    """Create a test allocation."""
    return AllocationResult(
        direction=direction,
        size_pct_q=size_pct_q,
        size_pct_scale=SIZE_PCT_SCALE,
        risk_cap=risk_cap,
        rationale=["test"],
        base_allocation_q=size_pct_q,
        entropy_factor=0.5,
        uncertainty_discount=0.8,
        final_factor=0.4,
    )


class TestCorrelationBuckets:
    """Test correlation bucket classification."""

    def test_low_correlation(self):
        assert get_correlation_bucket(0.0) == CorrelationBucket.LOW
        assert get_correlation_bucket(0.1) == CorrelationBucket.LOW
        assert get_correlation_bucket(0.29) == CorrelationBucket.LOW
        # Negative correlations use absolute value
        assert get_correlation_bucket(-0.2) == CorrelationBucket.LOW

    def test_medium_correlation(self):
        assert get_correlation_bucket(0.3) == CorrelationBucket.MEDIUM
        assert get_correlation_bucket(0.5) == CorrelationBucket.MEDIUM
        assert get_correlation_bucket(0.69) == CorrelationBucket.MEDIUM
        # Negative correlations use absolute value
        assert get_correlation_bucket(-0.5) == CorrelationBucket.MEDIUM

    def test_high_correlation(self):
        assert get_correlation_bucket(0.7) == CorrelationBucket.HIGH
        assert get_correlation_bucket(0.85) == CorrelationBucket.HIGH
        assert get_correlation_bucket(1.0) == CorrelationBucket.HIGH
        # Negative correlations use absolute value
        assert get_correlation_bucket(-0.9) == CorrelationBucket.HIGH


class TestPortfolioAllocate:
    """Test portfolio allocation logic."""

    def test_single_symbol_no_penalty(self):
        """Single symbol should have no correlation penalty."""
        allocations = {"BTCUSDT": make_allocation(size_pct_q=2500)}
        result = portfolio_allocate(allocations)

        assert len(result.allocations) == 1
        assert result.allocations["BTCUSDT"].adjusted_size_q == 2500
        assert result.allocations["BTCUSDT"].correlation_penalty == 0.0
        assert result.allocations["BTCUSDT"].is_anchor
        assert result.correlation_penalties_applied == 0

    def test_two_symbols_low_correlation(self):
        """Low correlation should not apply penalty."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500),
            "SOLUSDT": make_allocation(size_pct_q=2000),
        }
        corr_matrix = {("BTCUSDT", "SOLUSDT"): 0.2}

        result = portfolio_allocate(allocations, corr_matrix)

        assert result.allocations["BTCUSDT"].adjusted_size_q == 2500
        assert result.allocations["SOLUSDT"].adjusted_size_q == 2000  # No penalty
        assert result.correlation_penalties_applied == 0

    def test_two_symbols_high_correlation_same_direction(self):
        """High correlation + same direction should apply 60% penalty."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500, direction=Direction.LONG),
            "ETHUSDT": make_allocation(size_pct_q=2000, direction=Direction.LONG),
        }
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.85}

        result = portfolio_allocate(allocations, corr_matrix)

        # BTCUSDT is anchor (larger), no penalty
        assert result.allocations["BTCUSDT"].adjusted_size_q == 2500
        assert result.allocations["BTCUSDT"].is_anchor

        # ETHUSDT gets 60% penalty (0.4 factor)
        expected_eth = int(2000 * 0.4 + 0.5)
        assert result.allocations["ETHUSDT"].adjusted_size_q == expected_eth
        assert result.allocations["ETHUSDT"].correlation_penalty == 0.6
        assert result.correlation_penalties_applied == 1

    def test_two_symbols_high_correlation_opposite_direction(self):
        """Opposite directions = hedge, no penalty."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500, direction=Direction.LONG),
            "ETHUSDT": make_allocation(size_pct_q=2000, direction=Direction.SHORT),
        }
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.85}

        result = portfolio_allocate(allocations, corr_matrix)

        # No penalty because directions are opposite (hedging)
        assert result.allocations["ETHUSDT"].adjusted_size_q == 2000
        assert result.allocations["ETHUSDT"].correlation_penalty == 0.0
        assert result.correlation_penalties_applied == 0

    def test_medium_correlation_penalty(self):
        """Medium correlation should apply 30% penalty."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500),
            "ETHUSDT": make_allocation(size_pct_q=2000),
        }
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.5}

        result = portfolio_allocate(allocations, corr_matrix)

        # 30% penalty (0.7 factor)
        expected_eth = int(2000 * 0.7 + 0.5)
        assert result.allocations["ETHUSDT"].adjusted_size_q == expected_eth
        assert result.allocations["ETHUSDT"].correlation_penalty == pytest.approx(0.3)

    def test_exposure_cap_applied(self):
        """Total exposure should not exceed cap."""
        # Create allocations that would exceed 100%
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=6000),  # 60%
            "ETHUSDT": make_allocation(size_pct_q=5000),  # 50%
        }
        # Low correlation so no penalty
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.1}

        result = portfolio_allocate(allocations, corr_matrix, max_total_exposure_q=10000)

        # Total should be capped at 100%
        assert result.total_exposure_q == MAX_TOTAL_EXPOSURE_Q
        assert result.exposure_cap_applied
        # Both scaled proportionally
        total_original = 6000 + 5000
        scale = MAX_TOTAL_EXPOSURE_Q / total_original
        assert result.allocations["BTCUSDT"].adjusted_size_q == int(6000 * scale + 0.5)
        assert result.allocations["ETHUSDT"].adjusted_size_q == int(5000 * scale + 0.5)

    def test_explicit_anchor(self):
        """Explicit anchor should override largest position."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=3000),  # Larger
            "ETHUSDT": make_allocation(size_pct_q=2000),  # Smaller but explicit anchor
        }
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.85}

        result = portfolio_allocate(allocations, corr_matrix, anchor_symbol="ETHUSDT")

        # ETHUSDT is anchor (explicit), no penalty
        assert result.allocations["ETHUSDT"].is_anchor
        assert result.allocations["ETHUSDT"].adjusted_size_q == 2000

        # BTCUSDT gets penalty
        assert not result.allocations["BTCUSDT"].is_anchor
        assert result.allocations["BTCUSDT"].adjusted_size_q < 3000

    def test_flat_allocation_skipped(self):
        """Flat allocations should not be penalized."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500, direction=Direction.LONG),
            "ETHUSDT": make_allocation(size_pct_q=0, direction=Direction.FLAT),
        }

        result = portfolio_allocate(allocations)

        assert result.allocations["ETHUSDT"].adjusted_size_q == 0
        assert result.total_exposure_q == 2500

    def test_empty_portfolio(self):
        """Empty portfolio should return empty result."""
        result = portfolio_allocate({})

        assert result.allocations == {}
        assert result.total_exposure_q == 0
        assert result.portfolio_risk_cap == RiskCap.ZERO

    def test_portfolio_risk_cap_aggregation(self):
        """Portfolio risk cap should be highest individual."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=2500, risk_cap=RiskCap.LOW),
            "ETHUSDT": make_allocation(size_pct_q=2000, risk_cap=RiskCap.MEDIUM),
        }
        corr_matrix = {("BTCUSDT", "ETHUSDT"): 0.1}

        result = portfolio_allocate(allocations, corr_matrix)

        assert result.portfolio_risk_cap == RiskCap.MEDIUM

    def test_three_symbols_multiple_penalties(self):
        """Multiple non-anchor symbols can have penalties."""
        allocations = {
            "BTCUSDT": make_allocation(size_pct_q=3000),
            "ETHUSDT": make_allocation(size_pct_q=2000),
            "SOLUSDT": make_allocation(size_pct_q=1500),
        }
        # BTC-ETH: high, BTC-SOL: medium
        corr_matrix = {
            ("BTCUSDT", "ETHUSDT"): 0.85,
            ("BTCUSDT", "SOLUSDT"): 0.5,
        }

        result = portfolio_allocate(allocations, corr_matrix)

        # BTC is anchor (largest)
        assert result.allocations["BTCUSDT"].is_anchor
        assert result.allocations["BTCUSDT"].adjusted_size_q == 3000

        # ETH: high correlation, 60% penalty
        assert result.allocations["ETHUSDT"].adjusted_size_q == int(2000 * 0.4 + 0.5)

        # SOL: medium correlation, 30% penalty
        assert result.allocations["SOLUSDT"].adjusted_size_q == int(1500 * 0.7 + 0.5)

        assert result.correlation_penalties_applied == 2


class TestBuildCorrelationMatrix:
    """Test correlation matrix construction from state."""

    def test_builds_from_state(self):
        state = {
            "symbols": {
                "BTCUSDT": {},  # Anchor, no correlation field
                "ETHUSDT": {"rolling_correlation": 0.85},
            }
        }

        matrix = build_correlation_matrix_from_state(state, ["BTCUSDT", "ETHUSDT"])

        # Self-correlations
        assert matrix[("BTCUSDT", "BTCUSDT")] == 1.0
        assert matrix[("ETHUSDT", "ETHUSDT")] == 1.0

        # Cross-correlation (symmetric)
        assert matrix[("BTCUSDT", "ETHUSDT")] == 0.85
        assert matrix[("ETHUSDT", "BTCUSDT")] == 0.85

    def test_missing_correlation_not_added(self):
        state = {"symbols": {"BTCUSDT": {}, "ETHUSDT": {}}}

        matrix = build_correlation_matrix_from_state(state, ["BTCUSDT", "ETHUSDT"])

        # Only self-correlations
        assert ("BTCUSDT", "ETHUSDT") not in matrix


class TestPortfolioAllocationSerialization:
    """Test portfolio allocation serialization."""

    def test_to_dict(self):
        allocations = {"BTCUSDT": make_allocation(size_pct_q=2500)}
        result = portfolio_allocate(allocations)

        d = result.to_dict()

        assert "allocations" in d
        assert "BTCUSDT" in d["allocations"]
        assert d["allocations"]["BTCUSDT"]["direction"] == "long"
        assert d["total_exposure_q"] == 2500
        assert d["portfolio_risk_cap"] == "low"
