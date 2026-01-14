"""
v0.3 Intent Schema Invariant Tests.

CONSTITUTIONAL: These tests enforce that intents without expiry are invalid.

The single change that prevents most holding-too-long failure modes:
intents without expiry are schema-invalid.
"""

import pytest
from datetime import datetime, timedelta, timezone

from schemas.router_intent import (
    INTENT_SCHEMA_VERSION,
    EXIT_TRIGGERS,
    HORIZON_TOLERANCE_MINUTES,
    MAX_HORIZON_MINUTES,
    SUPPORTED_SCHEMA_VERSIONS,
    validate_router_intent,
    validate_router_intent_read,
    is_intent_expired,
    is_intent_actionable,
    count_horizon_violations,
    _validate_v03_expiry_fields,
)


def _make_valid_v03_payload(
    direction: str = "long",
    horizon_minutes: int = 15,
    exit_trigger: str = "time_stop",
) -> dict:
    """Create a minimal valid v0.3 intent payload."""
    source_ts = datetime.now(timezone.utc)
    valid_until_ts = source_ts + timedelta(minutes=horizon_minutes)

    return {
        "symbol": "BTCUSDT",
        "direction": direction,
        "size_pct_q": 250,
        "size_pct_scale": 10000,
        "risk_cap": "low",
        "rationale": ["test"],
        "schema_version": INTENT_SCHEMA_VERSION,
        "valid_until_ts": valid_until_ts.isoformat(),
        "horizon_minutes": horizon_minutes,
        "exit_trigger": exit_trigger,
        "entry_basis_event_id": "test-event-id-123",
    }


class TestIntentWithoutExpiryIsInvalid:
    """
    INVARIANT: An intent without expiry cannot be constructed.

    This is the test that prevents future-you from weakening the rule.
    """

    def test_missing_schema_version_fails(self):
        """Intent without schema_version must fail validation."""
        payload = _make_valid_v03_payload()
        del payload["schema_version"]

        with pytest.raises(ValueError, match="schema_version"):
            validate_router_intent(payload)

    def test_wrong_schema_version_fails(self):
        """Intent with wrong schema_version must fail validation."""
        payload = _make_valid_v03_payload()
        payload["schema_version"] = "v0.2"

        with pytest.raises(ValueError, match="schema_version"):
            validate_router_intent(payload)

    def test_missing_valid_until_ts_fails(self):
        """Intent without valid_until_ts must fail validation."""
        payload = _make_valid_v03_payload()
        del payload["valid_until_ts"]

        with pytest.raises(ValueError, match="valid_until_ts"):
            validate_router_intent(payload)

    def test_invalid_valid_until_ts_fails(self):
        """Intent with invalid timestamp must fail validation."""
        payload = _make_valid_v03_payload()
        payload["valid_until_ts"] = "not-a-timestamp"

        with pytest.raises(ValueError, match="valid_until_ts"):
            validate_router_intent(payload)

    def test_missing_horizon_minutes_fails(self):
        """Intent without horizon_minutes must fail validation."""
        payload = _make_valid_v03_payload()
        del payload["horizon_minutes"]

        with pytest.raises(ValueError, match="horizon_minutes"):
            validate_router_intent(payload)

    def test_zero_horizon_minutes_fails(self):
        """Intent with horizon_minutes=0 must fail validation."""
        payload = _make_valid_v03_payload()
        payload["horizon_minutes"] = 0

        with pytest.raises(ValueError, match="horizon_minutes"):
            validate_router_intent(payload)

    def test_negative_horizon_minutes_fails(self):
        """Intent with negative horizon_minutes must fail validation."""
        payload = _make_valid_v03_payload()
        payload["horizon_minutes"] = -15

        with pytest.raises(ValueError, match="horizon_minutes"):
            validate_router_intent(payload)

    def test_missing_exit_trigger_fails(self):
        """Intent without exit_trigger must fail validation."""
        payload = _make_valid_v03_payload()
        del payload["exit_trigger"]

        with pytest.raises(ValueError, match="exit_trigger"):
            validate_router_intent(payload)

    def test_invalid_exit_trigger_fails(self):
        """Intent with invalid exit_trigger must fail validation."""
        payload = _make_valid_v03_payload()
        payload["exit_trigger"] = "hold_forever"

        with pytest.raises(ValueError, match="exit_trigger"):
            validate_router_intent(payload)

    def test_missing_entry_basis_event_id_fails(self):
        """Intent without entry_basis_event_id must fail validation."""
        payload = _make_valid_v03_payload()
        del payload["entry_basis_event_id"]

        with pytest.raises(ValueError, match="entry_basis_event_id"):
            validate_router_intent(payload)


class TestHorizonConsistency:
    """
    INVARIANT: valid_until_ts - source_ts must match horizon_minutes.

    Prevents "expiry drift" bugs.
    """

    def test_horizon_consistency_passes(self):
        """Consistent horizon must pass validation."""
        source_ts = datetime.now(timezone.utc)
        horizon_minutes = 15
        valid_until_ts = source_ts + timedelta(minutes=horizon_minutes)

        payload = _make_valid_v03_payload(horizon_minutes=horizon_minutes)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        # Should not raise
        validate_router_intent(payload, source_ts=source_ts.isoformat())

    def test_horizon_drift_fails(self):
        """Inconsistent horizon must fail validation."""
        source_ts = datetime.now(timezone.utc)
        horizon_minutes = 15
        # Expiry says 60 minutes, but horizon_minutes says 15
        valid_until_ts = source_ts + timedelta(minutes=60)

        payload = _make_valid_v03_payload(horizon_minutes=horizon_minutes)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        with pytest.raises(ValueError, match="horizon drift"):
            validate_router_intent(payload, source_ts=source_ts.isoformat())

    def test_horizon_within_tolerance_passes(self):
        """Horizon within tolerance must pass validation."""
        source_ts = datetime.now(timezone.utc)
        horizon_minutes = 15
        # Within tolerance (15 + 0.5 minutes)
        valid_until_ts = source_ts + timedelta(minutes=15.5)

        payload = _make_valid_v03_payload(horizon_minutes=horizon_minutes)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        # Should not raise
        validate_router_intent(payload, source_ts=source_ts.isoformat())


class TestEnvelopeHorizonEquality:
    """
    INVARIANT: envelope.horizon_minutes must match intent.horizon_minutes.
    """

    def test_envelope_horizon_mismatch_fails(self):
        """Mismatched envelope horizon must fail validation."""
        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["envelope"] = {
            "horizon_minutes": 60,  # Mismatched
            "p_long": 0.5,
            "p_short": 0.0,
            "p_flat": 0.5,
            "p_vetoed": 0.0,
            "size_min": 0.01,
            "size_max": 0.05,
            "kernel": "mock_v0",
            "version": "0.0.1",
        }

        with pytest.raises(ValueError, match="envelope.horizon_minutes"):
            validate_router_intent(payload)

    def test_envelope_horizon_matches_passes(self):
        """Matching envelope horizon must pass validation."""
        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["envelope"] = {
            "horizon_minutes": 15,  # Matches
            "p_long": 0.5,
            "p_short": 0.0,
            "p_flat": 0.5,
            "p_vetoed": 0.0,
            "size_min": 0.01,
            "size_max": 0.05,
            "kernel": "mock_v0",
            "version": "0.0.1",
        }

        # Should not raise
        validate_router_intent(payload)


class TestValidPayloads:
    """Sanity checks for valid v0.3 payloads."""

    def test_minimal_valid_payload_passes(self):
        """Minimal valid v0.3 payload must pass validation."""
        payload = _make_valid_v03_payload()
        # Should not raise
        validate_router_intent(payload)

    def test_all_exit_triggers_valid(self):
        """All exit trigger values must be accepted."""
        for trigger in EXIT_TRIGGERS:
            payload = _make_valid_v03_payload(exit_trigger=trigger)
            # Should not raise
            validate_router_intent(payload)

    def test_various_horizons_valid(self):
        """Various horizon values must be accepted."""
        for horizon in [1, 5, 15, 30, 60, 240, 1440]:
            payload = _make_valid_v03_payload(horizon_minutes=horizon)
            # Should not raise
            validate_router_intent(payload)


class TestMaxHorizonInvariant:
    """INVARIANT: Horizon must not exceed MAX_HORIZON_MINUTES (24h)."""

    def test_horizon_at_max_passes(self):
        """Horizon at exactly max should pass."""
        payload = _make_valid_v03_payload(horizon_minutes=MAX_HORIZON_MINUTES)
        # Should not raise
        validate_router_intent(payload)

    def test_horizon_exceeds_max_fails(self):
        """Horizon exceeding max must fail."""
        payload = _make_valid_v03_payload(horizon_minutes=MAX_HORIZON_MINUTES + 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_router_intent(payload)


class TestExpiryAfterSourceInvariant:
    """INVARIANT: valid_until_ts must be after source_ts."""

    def test_expiry_after_source_passes(self):
        """Expiry after source should pass."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        # Should not raise
        validate_router_intent(payload, source_ts=source_ts.isoformat())

    def test_expiry_before_source_fails(self):
        """Expiry before source must fail."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts - timedelta(minutes=15)  # Before source

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        with pytest.raises(ValueError, match="must be after"):
            validate_router_intent(payload, source_ts=source_ts.isoformat())

    def test_expiry_equals_source_fails(self):
        """Expiry equal to source must fail."""
        source_ts = datetime.now(timezone.utc)

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = source_ts.isoformat()

        with pytest.raises(ValueError, match="must be after"):
            validate_router_intent(payload, source_ts=source_ts.isoformat())


class TestReadPathHistoricalCompatibility:
    """
    READ PATH: Historical events must not be bricked.

    v0.2 and pre-versioned events must pass validate_router_intent_read().
    """

    def _make_v02_payload(self) -> dict:
        """Create a valid v0.2 payload (no expiry fields)."""
        return {
            "symbol": "BTCUSDT",
            "direction": "long",
            "size_pct_q": 250,
            "size_pct_scale": 10000,
            "risk_cap": "low",
            "rationale": ["test"],
            # No schema_version, no expiry fields
        }

    def _make_legacy_payload(self) -> dict:
        """Create a valid legacy payload (float size, no version)."""
        return {
            "symbol": "BTCUSDT",
            "direction": "long",
            "size_pct": 0.025,
            "risk_cap": "normal",
            "rationale": ["test"],
            # No schema_version, no expiry fields
        }

    def test_v02_payload_passes_read_validation(self):
        """v0.2 payload (no expiry) must pass read validation."""
        payload = self._make_v02_payload()
        # Should not raise
        validate_router_intent_read(payload)

    def test_legacy_payload_passes_read_validation(self):
        """Legacy payload (float size) must pass read validation."""
        payload = self._make_legacy_payload()
        # Should not raise
        validate_router_intent_read(payload)

    def test_v03_payload_passes_read_validation(self):
        """v0.3 payload must also pass read validation."""
        payload = _make_valid_v03_payload()
        # Should not raise
        validate_router_intent_read(payload)

    def test_v02_payload_fails_write_validation(self):
        """v0.2 payload (no expiry) must FAIL write validation."""
        payload = self._make_v02_payload()
        with pytest.raises(ValueError, match="schema_version"):
            validate_router_intent(payload)

    def test_legacy_payload_fails_write_validation(self):
        """Legacy payload must FAIL write validation."""
        payload = self._make_legacy_payload()
        with pytest.raises(ValueError, match="schema_version"):
            validate_router_intent(payload)

    def test_unsupported_schema_version_fails_read(self):
        """Unsupported schema version must fail read validation."""
        payload = self._make_v02_payload()
        payload["schema_version"] = "v99.0"

        with pytest.raises(ValueError, match="unsupported schema_version"):
            validate_router_intent_read(payload)

    def test_supported_schema_versions(self):
        """Verify supported schema versions are correct."""
        assert None in SUPPORTED_SCHEMA_VERSIONS  # Pre-versioned
        assert "v0.2" in SUPPORTED_SCHEMA_VERSIONS
        assert "v0.3" in SUPPORTED_SCHEMA_VERSIONS


class TestConsumerBoundaryExpiry:
    """
    CONSUMER BOUNDARY: Expired intents must be non-actionable.

    This closes the loop: schema prevents creation of bad intents,
    consumer boundary prevents misuse of valid intents after expiry.
    """

    def test_unexpired_intent_not_expired(self):
        """Intent before expiry should not be expired."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)
        now_ts = source_ts + timedelta(minutes=5)  # 5 minutes in

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        assert is_intent_expired(payload, now_ts.isoformat()) is False

    def test_expired_intent_is_expired(self):
        """Intent after expiry should be expired."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)
        now_ts = source_ts + timedelta(minutes=20)  # 5 minutes past expiry

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        assert is_intent_expired(payload, now_ts.isoformat()) is True

    def test_exactly_at_expiry_is_expired(self):
        """Intent exactly at expiry should be expired (>= not >)."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        # now_ts == valid_until_ts
        assert is_intent_expired(payload, valid_until_ts.isoformat()) is True

    def test_legacy_intent_without_expiry_not_expired(self):
        """Legacy intent without valid_until_ts should not be expired."""
        payload = {
            "symbol": "BTCUSDT",
            "direction": "long",
            "size_pct": 0.025,
            "risk_cap": "normal",
            "rationale": ["test"],
            # No valid_until_ts (legacy)
        }
        now_ts = datetime.now(timezone.utc)

        # Legacy intents are never expired (historical compatibility)
        assert is_intent_expired(payload, now_ts.isoformat()) is False

    def test_invalid_timestamp_fails_safe_to_expired(self):
        """Malformed timestamp should fail safe to expired."""
        payload = _make_valid_v03_payload()
        payload["valid_until_ts"] = "not-a-timestamp"

        now_ts = datetime.now(timezone.utc)
        # Parse failure = fail safe = expired
        assert is_intent_expired(payload, now_ts.isoformat()) is True


class TestConsumerBoundaryActionable:
    """
    CONSUMER BOUNDARY: is_intent_actionable() is the canonical check.

    Combines expiry check + structural validation.
    """

    def test_valid_unexpired_intent_is_actionable(self):
        """Valid intent before expiry should be actionable."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)
        now_ts = source_ts + timedelta(minutes=5)

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        assert is_intent_actionable(payload, now_ts.isoformat()) is True

    def test_valid_expired_intent_not_actionable(self):
        """Valid intent after expiry should NOT be actionable."""
        source_ts = datetime.now(timezone.utc)
        valid_until_ts = source_ts + timedelta(minutes=15)
        now_ts = source_ts + timedelta(minutes=20)

        payload = _make_valid_v03_payload(horizon_minutes=15)
        payload["valid_until_ts"] = valid_until_ts.isoformat()

        assert is_intent_actionable(payload, now_ts.isoformat()) is False

    def test_invalid_unexpired_intent_not_actionable(self):
        """Invalid intent (bad structure) should NOT be actionable."""
        now_ts = datetime.now(timezone.utc)
        valid_until_ts = now_ts + timedelta(minutes=15)

        payload = {
            "symbol": "BTCUSDT",
            "direction": "invalid_direction",  # Bad
            "size_pct_q": 250,
            "size_pct_scale": 10000,
            "risk_cap": "low",
            "rationale": ["test"],
            "schema_version": INTENT_SCHEMA_VERSION,
            "valid_until_ts": valid_until_ts.isoformat(),
            "horizon_minutes": 15,
            "exit_trigger": "time_stop",
            "entry_basis_event_id": "test-id",
        }

        # Structurally invalid = not actionable
        assert is_intent_actionable(payload, now_ts.isoformat()) is False

    def test_legacy_intent_is_actionable(self):
        """Legacy intent (no expiry) should be actionable for compatibility."""
        payload = {
            "symbol": "BTCUSDT",
            "direction": "long",
            "size_pct": 0.025,
            "risk_cap": "normal",
            "rationale": ["test"],
        }
        now_ts = datetime.now(timezone.utc)

        # Legacy intents are actionable (never expire)
        assert is_intent_actionable(payload, now_ts.isoformat()) is True


class TestHorizonViolationMetric:
    """
    REGRESSION METRIC: count_horizon_violations must be zero for v0.3.

    This is the permanent regression check that proves the system
    is respecting intent validity windows.
    """

    def test_no_violations_when_all_within_horizon(self):
        """Intents acted upon within horizon should have zero violations."""
        source_ts = datetime.now(timezone.utc)

        intents = [
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=5)).isoformat(),
            },
            {
                "valid_until_ts": (source_ts + timedelta(minutes=30)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=10)).isoformat(),
            },
        ]

        violations = count_horizon_violations(
            intents, lambda i: i.get("decision_ts")
        )
        assert violations == 0

    def test_counts_violations_when_past_horizon(self):
        """Intents acted upon after expiry should be counted."""
        source_ts = datetime.now(timezone.utc)

        intents = [
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=5)).isoformat(),  # OK
            },
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=20)).isoformat(),  # VIOLATION
            },
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=60)).isoformat(),  # VIOLATION
            },
        ]

        violations = count_horizon_violations(
            intents, lambda i: i.get("decision_ts")
        )
        assert violations == 2

    def test_exactly_at_expiry_is_violation(self):
        """Decision exactly at expiry is a violation (>= not >)."""
        source_ts = datetime.now(timezone.utc)
        expiry = source_ts + timedelta(minutes=15)

        intents = [
            {
                "valid_until_ts": expiry.isoformat(),
                "decision_ts": expiry.isoformat(),  # Exactly at expiry
            },
        ]

        violations = count_horizon_violations(
            intents, lambda i: i.get("decision_ts")
        )
        assert violations == 1

    def test_legacy_intents_excluded_from_count(self):
        """Legacy intents without valid_until_ts should not count as violations."""
        source_ts = datetime.now(timezone.utc)

        intents = [
            # Legacy intent (no valid_until_ts)
            {
                "decision_ts": (source_ts + timedelta(days=365)).isoformat(),
            },
            # v0.3 intent within horizon
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                "decision_ts": (source_ts + timedelta(minutes=5)).isoformat(),
            },
        ]

        violations = count_horizon_violations(
            intents, lambda i: i.get("decision_ts")
        )
        assert violations == 0

    def test_empty_list_returns_zero(self):
        """Empty intent list should return zero violations."""
        violations = count_horizon_violations([], lambda i: i.get("decision_ts"))
        assert violations == 0

    def test_missing_decision_ts_not_counted(self):
        """Intents with no decision_ts should not be counted."""
        source_ts = datetime.now(timezone.utc)

        intents = [
            {
                "valid_until_ts": (source_ts + timedelta(minutes=15)).isoformat(),
                # No decision_ts
            },
        ]

        violations = count_horizon_violations(
            intents, lambda i: i.get("decision_ts")
        )
        assert violations == 0
