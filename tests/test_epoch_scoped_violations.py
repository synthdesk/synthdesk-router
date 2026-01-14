"""
Epoch-Scoped Violation Tests.

Validates that invariant violations are:
1. Recorded passively in state (epoch-scoped)
2. Evaluated by authority plane with severity gating

ARCHITECTURE:
- state.py: Passive memory. Records violations, tracks degraded symbols.
            NEVER decides authority. Only stores facts.
- authority.py: Policy plane. Decides demotion based on severity.
            Critical -> demote. Warning -> no demotion.

SEVERITY GATING (authority plane policy):
1. severity="critical" -> authority demotion (v0.2 -> v0.1)
2. severity="warning" (or other) -> symbol degraded, NO authority demotion

EPOCH SCOPING (state recording):
1. Violations BEFORE promoted_at are NOT recorded
2. Violations AT or AFTER promoted_at ARE recorded
3. No authority_epoch_ts (v0.1 mode) -> all violations recorded

This ensures:
- Network blips (warnings) don't nuke authority
- True structural corruption (critical) still demotes
- Historical violations from prior epochs cannot poison new authority binding
"""

from router.state import RouterState
from router.authority import create_severity_gated_violation_check


def make_violation_event(timestamp: str, severity: str = "warning", symbol: str = "BTCUSDT") -> dict:
    """Create a minimal invariant.violation event."""
    return {
        "event_id": "test-violation-id",
        "event_type": "invariant.violation",
        "timestamp": timestamp,
        "payload": {
            "invariant_id": "test.violation",
            "severity": severity,
            "symbol": symbol,
        },
    }


def make_regime_event(timestamp: str, symbol: str = "BTCUSDT") -> dict:
    """Create a market.regime event for auto-recovery testing."""
    return {
        "event_id": "test-regime-id",
        "event_type": "market.regime",
        "timestamp": timestamp,
        "payload": {
            "symbol": symbol,
            "regime": "drift",
        },
    }


# ============================================
# STATE PASSIVE RECORDING TESTS
# ============================================


def test_state_records_warning_violation():
    """State should record warning violations (passive memory)."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="warning", symbol="BTCUSDT")
    state.update_from_event(event)

    # State should record the violation
    ts, severity, symbol = state.get_last_violation()
    assert ts == "2026-01-10T12:00:00+00:00"
    assert severity == "warning"
    assert symbol == "BTCUSDT"

    # Warning should degrade symbol
    assert state.is_symbol_degraded("BTCUSDT") is True


def test_state_records_critical_violation():
    """State should record critical violations (passive memory)."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="critical", symbol="BTCUSDT")
    state.update_from_event(event)

    # State should record the violation
    ts, severity, symbol = state.get_last_violation()
    assert ts == "2026-01-10T12:00:00+00:00"
    assert severity == "critical"

    # Critical should NOT degrade symbol (authority handles it differently)
    assert state.is_symbol_degraded("BTCUSDT") is False


def test_state_ignores_pre_epoch_violation():
    """Violations before authority epoch should not be recorded."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Violation from before epoch
    event = make_violation_event("2025-12-21T00:00:00+00:00", severity="critical")
    state.update_from_event(event)

    # Should NOT be recorded
    ts, severity, symbol = state.get_last_violation()
    assert ts is None
    assert severity is None


def test_state_default_severity_is_warning():
    """Missing severity field should default to warning."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    event = {
        "event_id": "test-violation-id",
        "event_type": "invariant.violation",
        "timestamp": "2026-01-10T12:00:00+00:00",
        "payload": {
            "invariant_id": "test.violation",
            # no severity field
        },
    }
    state.update_from_event(event)

    ts, severity, symbol = state.get_last_violation()
    assert severity == "warning"


# ============================================
# AUTHORITY PLANE SEVERITY GATING TESTS
# ============================================


def test_authority_check_ignores_warning():
    """Authority check should NOT trigger demotion for warnings."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")
    check = create_severity_gated_violation_check(state.get_last_violation)

    # Warning violation
    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="warning")
    state.update_from_event(event)

    # Check should return None (no demotion trigger)
    result = check()
    assert result is None


def test_authority_check_triggers_on_critical():
    """Authority check SHOULD trigger demotion for critical violations."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")
    check = create_severity_gated_violation_check(state.get_last_violation)

    # Critical violation
    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="critical", symbol="BTCUSDT")
    state.update_from_event(event)

    # Check should return demotion trigger
    result = check()
    assert result is not None
    assert "critical_violation" in result


def test_authority_check_no_repeat_trigger():
    """Authority check should not re-trigger on same violation timestamp."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")
    check = create_severity_gated_violation_check(state.get_last_violation)

    # Critical violation
    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="critical")
    state.update_from_event(event)

    # First check triggers
    result1 = check()
    assert result1 is not None

    # Second check on same violation should NOT trigger again
    result2 = check()
    assert result2 is None


def test_authority_check_triggers_on_new_critical():
    """Authority check should trigger on new critical violation."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")
    check = create_severity_gated_violation_check(state.get_last_violation)

    # First critical violation
    state.update_from_event(make_violation_event("2026-01-10T12:00:00+00:00", severity="critical"))
    result1 = check()
    assert result1 is not None

    # Second check doesn't re-trigger
    result2 = check()
    assert result2 is None

    # NEW critical violation (different timestamp)
    state.update_from_event(make_violation_event("2026-01-10T13:00:00+00:00", severity="critical"))
    result3 = check()
    assert result3 is not None  # New violation triggers


# ============================================
# DEGRADED SYMBOL MANAGEMENT TESTS
# ============================================


def test_degraded_symbol_auto_recovers_on_regime_event():
    """Degraded symbols should auto-recover when they emit healthy regime events."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Warning violation degrades symbol
    event = make_violation_event("2026-01-10T12:00:00+00:00", severity="warning", symbol="BTCUSDT")
    state.update_from_event(event)
    assert state.is_symbol_degraded("BTCUSDT") is True

    # Regime event clears degraded status (auto-recovery)
    regime_event = make_regime_event("2026-01-10T12:01:00+00:00", symbol="BTCUSDT")
    state.update_from_event(regime_event)
    assert state.is_symbol_degraded("BTCUSDT") is False


def test_get_degraded_symbols_returns_copy():
    """get_degraded_symbols should return a copy, not internal state."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    state.update_from_event(make_violation_event("2026-01-10T12:00:00+00:00", symbol="BTCUSDT"))
    degraded = state.get_degraded_symbols()

    # Modifying returned set should not affect internal state
    degraded.add("FAKE")
    assert "FAKE" not in state.get_degraded_symbols()


def test_clear_degraded_manual():
    """clear_degraded should manually clear degraded status."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    state.update_from_event(make_violation_event("2026-01-10T12:00:00+00:00", symbol="BTCUSDT"))
    assert state.is_symbol_degraded("BTCUSDT") is True

    state.clear_degraded("BTCUSDT")
    assert state.is_symbol_degraded("BTCUSDT") is False


def test_multiple_symbols_degraded_independently():
    """Each symbol's degraded status is independent."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Degrade BTC
    state.update_from_event(make_violation_event("2026-01-10T12:00:00+00:00", symbol="BTCUSDT"))
    # Degrade ETH
    state.update_from_event(make_violation_event("2026-01-10T12:01:00+00:00", symbol="ETHUSDT"))

    assert state.is_symbol_degraded("BTCUSDT") is True
    assert state.is_symbol_degraded("ETHUSDT") is True

    # Recover BTC only
    state.update_from_event(make_regime_event("2026-01-10T12:02:00+00:00", symbol="BTCUSDT"))

    assert state.is_symbol_degraded("BTCUSDT") is False
    assert state.is_symbol_degraded("ETHUSDT") is True


# ============================================
# EPOCH SCOPING TESTS
# ============================================


def test_epoch_comparison_is_lexicographic():
    """ISO8601 timestamps compare correctly via string comparison."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Edge case: timestamp string that's lexicographically less
    event = make_violation_event("2026-01-09T23:59:59+00:00", severity="critical")
    state.update_from_event(event)

    # Should NOT be recorded (before epoch)
    ts, _, _ = state.get_last_violation()
    assert ts is None


def test_no_epoch_all_violations_recorded():
    """Without authority_epoch_ts, all violations should be recorded."""
    state = RouterState(authority_epoch_ts=None)

    # Ancient violation
    event = make_violation_event("2020-01-01T00:00:00+00:00", severity="critical")
    state.update_from_event(event)

    # Should be recorded
    ts, severity, _ = state.get_last_violation()
    assert ts == "2020-01-01T00:00:00+00:00"
    assert severity == "critical"
