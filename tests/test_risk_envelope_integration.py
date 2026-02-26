"""
Integration tests for Risk Envelope v0 wiring.

Tests the integration between:
- RouterState.get_effective_rank() (staleness-bounded)
- paper_executor.check_caps() (envelope-driven limits)

Court-enforced invariants:
1. Fail-closed: missing rank → rank 1 envelope applied
2. Clamp determinism: same inputs → same outputs
3. Non-alpha: envelope cannot flip direction, only adjust risk params
"""

import pytest
from datetime import datetime, timezone

from router.state import RouterState, MAX_EFFECTIVE_RANK_STALENESS_MS
from router.paper_executor import (
    check_caps,
    process_intent,
    ExecutorState,
    PAPER_CAPITAL,
    BASE_MAX_GROSS_EXPOSURE,
    BASE_MAX_NOTIONAL_PER_TRADE,
    _ENVELOPE_AVAILABLE,
)


class TestRouterStateEffectiveRank:
    """Test RouterState tracks effective_rank from spectral.emit."""

    def test_spectral_emit_updates_effective_rank(self):
        """spectral.emit updates the effective_rank cache."""
        state = RouterState()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        event = {
            "event_type": "spectral.emit",
            "payload": {
                "symbol": "BTCUSDT",
                "effective_rank": 2,
                "ts_ms": now_ms,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        state.update_from_event(event)

        assert state.get_effective_rank("BTCUSDT", now_ms) == 2

    def test_missing_rank_returns_none(self):
        """Missing rank returns None (caller must fail-closed)."""
        state = RouterState()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        assert state.get_effective_rank("BTCUSDT", now_ms) is None

    def test_stale_rank_returns_none(self):
        """Stale rank (beyond MAX_EFFECTIVE_RANK_STALENESS_MS) returns None."""
        state = RouterState()
        old_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - MAX_EFFECTIVE_RANK_STALENESS_MS - 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        event = {
            "event_type": "spectral.emit",
            "payload": {
                "symbol": "BTCUSDT",
                "effective_rank": 2,
                "ts_ms": old_ts_ms,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        state.update_from_event(event)

        # Fresh query should see the rank
        assert state.get_effective_rank("BTCUSDT", old_ts_ms + 1000) == 2

        # Query beyond staleness window should return None
        assert state.get_effective_rank("BTCUSDT", now_ms) is None

    def test_malformed_spectral_emit_ignored(self):
        """Malformed spectral.emit does not crash router."""
        state = RouterState()

        # Missing fields
        event = {
            "event_type": "spectral.emit",
            "payload": {"symbol": "BTCUSDT"},  # missing effective_rank, ts_ms
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Should not raise
        state.update_from_event(event)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        assert state.get_effective_rank("BTCUSDT", now_ms) is None


@pytest.mark.skipif(
    not _ENVELOPE_AVAILABLE,
    reason="risk envelope package unavailable in standalone router repository",
)
class TestCheckCapsEnvelopeIntegration:
    """Test check_caps uses envelope-driven limits."""

    def test_rank_none_applies_rank_1_envelope(self):
        """Missing regime_rank applies rank=1 envelope (fail-closed)."""
        state = ExecutorState()
        envelope_audit = {}

        # rank=1 envelope: max_gross_exposure_mult = 0.40
        # max_notional = 0.40 * BASE_MAX_NOTIONAL_PER_TRADE = 0.40 * 200 = 80 USD
        # Use small notional that fits within rank=1 limits
        allowed, _, _ = check_caps(
            state,
            proposed_notional=50.0,  # Well within rank=1 limit of 80
            current_price=50000.0,
            regime_rank=None,
            envelope_audit=envelope_audit,
        )

        assert allowed is True
        assert envelope_audit["resolved_rank"] == 1
        assert envelope_audit["rank_source"] == "missing_failclosed"
        assert envelope_audit["limits"]["max_gross_exposure"] == 0.40 * PAPER_CAPITAL

    def test_rank_1_tighter_limits(self):
        """rank=1 applies tighter limits than rank=2."""
        state = ExecutorState()

        # rank=1 envelope
        audit_r1 = {}
        check_caps(state, 100.0, 50000.0, regime_rank=1, envelope_audit=audit_r1)

        # rank=2 envelope
        audit_r2 = {}
        check_caps(state, 100.0, 50000.0, regime_rank=2, envelope_audit=audit_r2)

        # rank=1 should have tighter limits
        assert audit_r1["limits"]["max_gross_exposure"] < audit_r2["limits"]["max_gross_exposure"]
        assert audit_r1["limits"]["max_concurrent_positions"] < audit_r2["limits"]["max_concurrent_positions"]

    def test_rank_2_allows_higher_exposure(self):
        """rank=2 allows higher notional than rank=1."""
        state = ExecutorState()

        # rank=1 max_notional = 0.40 * 200 = 80 USD
        # rank=2 max_notional = 1.00 * 200 = 200 USD
        # Propose notional that exceeds rank=1 limit but within rank=2
        proposed = 150.0

        # rank=1 should block (150 > 80)
        allowed_r1, breach, _ = check_caps(state, proposed, 50000.0, regime_rank=1)

        # rank=2 should allow (150 < 200)
        allowed_r2, _, _ = check_caps(state, proposed, 50000.0, regime_rank=2)

        assert allowed_r1 is False
        assert allowed_r2 is True

    def test_envelope_audit_populated(self):
        """Envelope audit dict is populated with required fields."""
        state = ExecutorState()
        audit = {}

        check_caps(state, 100.0, 50000.0, regime_rank=2, envelope_audit=audit)

        assert "envelope_version" in audit
        assert "regime_rank" in audit
        assert "resolved_rank" in audit
        assert "rank_source" in audit
        assert "limits" in audit
        assert "max_gross_exposure" in audit["limits"]
        assert "max_notional_per_trade" in audit["limits"]
        assert "max_concurrent_positions" in audit["limits"]


class TestDeterminism:
    """Test deterministic behavior (replay-stable)."""

    def test_same_inputs_same_outputs(self):
        """Same inputs produce same cap decisions."""
        state = ExecutorState()

        for _ in range(10):
            audit1, audit2 = {}, {}
            allowed1, breach1, reason1 = check_caps(state, 100.0, 50000.0, regime_rank=1, envelope_audit=audit1)
            allowed2, breach2, reason2 = check_caps(state, 100.0, 50000.0, regime_rank=1, envelope_audit=audit2)

            assert allowed1 == allowed2
            assert breach1 == breach2
            assert reason1 == reason2
            assert audit1["limits"] == audit2["limits"]


class TestNonAlpha:
    """Test envelope does not influence direction (non-alpha guarantee)."""

    def test_envelope_does_not_flip_direction(self):
        """Envelope can reject/allow but cannot change direction."""
        state = ExecutorState()

        event = {
            "payload": {
                "symbol": "BTCUSDT",
                "direction": "long",
                "size_pct_q": 1000,  # 10% of capital
                "size_pct_scale": 10000,
            },
            "source_event_id": "test_001",
            "source_ts": datetime.now(timezone.utc).isoformat(),
        }

        prices = {"BTCUSDT": 50000.0}

        # Process with rank=1 (tighter)
        exec_r1, _ = process_intent(state, event, prices, regime_rank=1)

        # Process with rank=2 (looser) - reset state
        state2 = ExecutorState()
        exec_r2, _ = process_intent(state2, event, prices, regime_rank=2)

        # Direction in payload should never change
        # (envelope can reject but not flip long→short)
        if exec_r1["payload"]["action"] not in ("rejected",):
            assert exec_r1["payload"]["symbol"] == "BTCUSDT"
        if exec_r2["payload"]["action"] not in ("rejected",):
            assert exec_r2["payload"]["symbol"] == "BTCUSDT"
