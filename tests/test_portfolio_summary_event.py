"""
Portfolio summary event emission invariants.
"""

import json
from pathlib import Path

from router.authority import AuthorityLevel, AuthorityState
from router.main import run_replay
from synthdesk_spine.event_types import LISTENER_START, MARKET_REGIME, ROUTER_PORTFOLIO_V0


def _event(event_id: str, event_type: str, ts: str, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": ts,
        "payload": payload,
    }


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_portfolio_summary_event_emits_once_and_is_deterministic(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROUTER_PORTFOLIO_ENABLE", "1")
    input_events = [
        _event(
            "evt_regime_btc",
            MARKET_REGIME,
            "2026-02-01T00:00:01Z",
            {
                "symbol": "BTCUSDT",
                "regime": "drift_persist",
                "confidence": 0.95,
                "metrics": {"z_mean": 1.1, "z_std": 0.4},
            },
        ),
        _event(
            "evt_regime_eth",
            MARKET_REGIME,
            "2026-02-01T00:00:02Z",
            {
                "symbol": "ETHUSDT",
                "regime": "drift_persist",
                "confidence": 0.95,
                "metrics": {"z_mean": 1.0, "z_std": 0.4, "rolling_correlation": 0.95},
            },
        ),
        _event(
            "evt_regime_sol",
            MARKET_REGIME,
            "2026-02-01T00:00:03Z",
            {
                "symbol": "SOLUSDT",
                "regime": "drift_persist",
                "confidence": 0.95,
                "metrics": {"z_mean": 0.9, "z_std": 0.4, "rolling_correlation": 0.60},
            },
        ),
        _event(
            "evt_listener",
            LISTENER_START,
            "2026-02-01T00:00:09Z",
            {},
        ),
    ]

    input_path = tmp_path / "input_spine.jsonl"
    out_a = tmp_path / "out_a.jsonl"
    out_b = tmp_path / "out_b.jsonl"
    _write_jsonl(input_path, input_events)

    authority = AuthorityState(
        level=AuthorityLevel.V0_2,
        promoted_at="2026-02-01T00:00:00Z",
    )

    run_replay(input_spine=input_path, output_spine=out_a, authority_state=authority)
    run_replay(input_spine=input_path, output_spine=out_b, authority_state=authority)

    # Replay determinism still holds with portfolio summary emission.
    assert out_a.read_bytes() == out_b.read_bytes()

    emitted = _read_jsonl(out_a)
    portfolio_events = [
        row
        for row in emitted
        if row.get("event_type") == ROUTER_PORTFOLIO_V0
        and row.get("source_event_id") == "evt_listener"
    ]
    assert len(portfolio_events) == 1

    payload = portfolio_events[0]["payload"]
    assert payload["anchor_symbol"] == "BTCUSDT"
    assert payload["symbols"] == sorted(payload["symbols"], key=lambda item: item["symbol"])

    total_before = sum(item["orig_size_q"] for item in payload["symbols"])
    total_after = sum(item["adjusted_size_q"] for item in payload["symbols"])
    assert payload["total_before_q"] == total_before
    assert payload["total_after_q"] == total_after
    assert payload["total_after_q"] <= 10000

    by_symbol = {item["symbol"]: item for item in payload["symbols"]}
    assert by_symbol["BTCUSDT"]["corr_bucket"] == "anchor"
    assert by_symbol["ETHUSDT"]["corr_bucket"] == "high"
    assert by_symbol["SOLUSDT"]["corr_bucket"] == "medium"
    assert by_symbol["ETHUSDT"]["corr_value"] == 0.95
    assert by_symbol["SOLUSDT"]["corr_value"] == 0.6
