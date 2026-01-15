"""
Tests for envelope ↔ intent binding integrity.
"""

import pytest

from router.binding import (
    BindingCollisionError,
    MissingBindingError,
    hash_bound_ids,
    validate_envelope_bindings,
)


def test_binding_by_source_event_id_same_minute():
    intents = [
        {"source_event_id": "evt_a", "symbol": "BTCUSDT", "intent_id": "i1"},
        {"source_event_id": "evt_b", "symbol": "ETHUSDT", "intent_id": "i2"},
    ]
    envelopes = [
        {"source_event_id": "evt_b", "symbol": "ETHUSDT", "ts_utc": "2026-01-12T00:00:10Z"},
        {"source_event_id": "evt_a", "symbol": "BTCUSDT", "ts_utc": "2026-01-12T00:00:20Z"},
    ]

    report = validate_envelope_bindings(intents, envelopes)
    assert report["n_bound"] == 2
    assert report["bound_ids_sha256"] == hash_bound_ids(["evt_b", "evt_a"])


def test_binding_collision_on_duplicate_intent_ids():
    intents = [
        {"source_event_id": "evt_dup", "symbol": "BTCUSDT", "intent_id": "i1"},
        {"source_event_id": "evt_dup", "symbol": "BTCUSDT", "intent_id": "i2"},
    ]
    envelopes = [
        {"source_event_id": "evt_dup", "symbol": "BTCUSDT"},
    ]

    with pytest.raises(BindingCollisionError) as exc:
        validate_envelope_bindings(intents, envelopes)

    report = exc.value.report
    assert report["n_collisions"] == 1
    assert report["collisions"][0]["source_event_id"] == "evt_dup"


def test_binding_missing_intent():
    intents = [
        {"source_event_id": "evt_a", "symbol": "BTCUSDT"},
    ]
    envelopes = [
        {"source_event_id": "evt_missing", "symbol": "BTCUSDT"},
    ]

    with pytest.raises(MissingBindingError) as exc:
        validate_envelope_bindings(intents, envelopes)

    report = exc.value.report
    assert report["n_missing"] == 1
    assert report["missing_ids"]["evt_missing"] == 1
