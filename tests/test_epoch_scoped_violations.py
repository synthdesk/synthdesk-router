"""
Epoch-Scoped Violation Tests.

Validates that invariant violations are scoped to authority epochs:
1. Violations BEFORE promoted_at do NOT trigger demotion
2. Violations AT or AFTER promoted_at DO trigger demotion
3. No authority_epoch_ts (v0.1 mode) -> all violations count

This ensures historical violations from prior epochs cannot poison
a new authority binding established via promotion certificate.
"""

from router.state import RouterState


def make_violation_event(timestamp: str) -> dict:
    """Create a minimal invariant.violation event."""
    return {
        "event_id": "test-violation-id",
        "event_type": "invariant.violation",
        "timestamp": timestamp,
        "payload": {
            "invariant_id": "test.violation",
            "severity": "warning",
        },
    }


def test_violation_before_epoch_does_not_activate():
    """Violations before authority epoch start should be ignored."""
    # Authority epoch starts at 2026-01-10T00:00:00
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Violation from 2025-12-21 (before epoch)
    event = make_violation_event("2025-12-21T00:46:16.601321+00:00")
    state.update_from_event(event)

    # Should NOT be active - violation predates authority epoch
    assert state.is_violation_active() is False
    assert state.system["last_violation_ts"] is None


def test_violation_at_epoch_activates():
    """Violations at exactly the epoch start should activate."""
    epoch_ts = "2026-01-10T00:00:00+00:00"
    state = RouterState(authority_epoch_ts=epoch_ts)

    # Violation at exact epoch start
    event = make_violation_event(epoch_ts)
    state.update_from_event(event)

    # Should be active - violation is within epoch
    assert state.is_violation_active() is True
    assert state.system["last_violation_ts"] == epoch_ts


def test_violation_after_epoch_activates():
    """Violations after authority epoch start should activate."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Violation from 2026-01-10T12:00:00 (after epoch)
    violation_ts = "2026-01-10T12:00:00+00:00"
    event = make_violation_event(violation_ts)
    state.update_from_event(event)

    # Should be active - violation is within epoch
    assert state.is_violation_active() is True
    assert state.system["last_violation_ts"] == violation_ts


def test_no_epoch_all_violations_count():
    """Without authority_epoch_ts, all violations should activate (v0.1 behavior)."""
    state = RouterState(authority_epoch_ts=None)

    # Ancient violation
    event = make_violation_event("2020-01-01T00:00:00+00:00")
    state.update_from_event(event)

    # Should be active - no epoch filtering
    assert state.is_violation_active() is True


def test_multiple_violations_mixed_epochs():
    """Only violations within epoch should count, regardless of order."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Process violations in mixed order
    events = [
        make_violation_event("2025-12-01T00:00:00+00:00"),  # Before - ignored
        make_violation_event("2025-12-15T00:00:00+00:00"),  # Before - ignored
        make_violation_event("2025-12-31T23:59:59+00:00"),  # Before - ignored
    ]

    for event in events:
        state.update_from_event(event)

    # None should activate - all before epoch
    assert state.is_violation_active() is False

    # Now a violation within epoch
    state.update_from_event(make_violation_event("2026-01-10T00:00:01+00:00"))
    assert state.is_violation_active() is True


def test_violation_sticky_within_epoch():
    """Once activated by an in-epoch violation, remains active."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Activate with in-epoch violation
    state.update_from_event(make_violation_event("2026-01-10T01:00:00+00:00"))
    assert state.is_violation_active() is True

    # Process other events - should remain active
    state.update_from_event({
        "event_type": "listener.start",
        "timestamp": "2026-01-10T02:00:00+00:00",
        "payload": {},
    })
    assert state.is_violation_active() is True


def test_epoch_comparison_is_lexicographic():
    """ISO8601 timestamps compare correctly via string comparison."""
    state = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")

    # Edge case: timestamp string that's lexicographically less
    # "2026-01-09T23:59:59" < "2026-01-10T00:00:00" lexicographically
    event = make_violation_event("2026-01-09T23:59:59+00:00")
    state.update_from_event(event)
    assert state.is_violation_active() is False

    # Edge case: timestamp string that's lexicographically greater
    # "2026-01-10T00:00:01" > "2026-01-10T00:00:00" lexicographically
    state2 = RouterState(authority_epoch_ts="2026-01-10T00:00:00+00:00")
    event2 = make_violation_event("2026-01-10T00:00:01+00:00")
    state2.update_from_event(event2)
    assert state2.is_violation_active() is True
