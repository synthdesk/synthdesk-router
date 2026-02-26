"""
Replay determinism and emission ordering invariants.
"""

from collections import Counter
import json
import random
from pathlib import Path

from router.authority import AuthorityLevel, AuthorityState
from router.main import run_replay


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


def test_replay_is_byte_identical_and_listener_emits_sorted_symbols(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROUTER_PORTFOLIO_ENABLE", "0")

    symbols = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
    rng = random.Random(7)
    rng.shuffle(symbols)

    input_events = []
    for idx, symbol in enumerate(symbols, start=1):
        input_events.append(
            _event(
                event_id=f"evt_regime_{idx}",
                event_type="market.regime",
                ts=f"2026-01-01T00:00:0{idx}Z",
                payload={
                    "symbol": symbol,
                    "regime": "drift_persist",
                    "confidence": 0.95,
                },
            )
        )

    # Triggers all-symbol synthesis in replay path.
    input_events.append(
        _event(
            event_id="evt_listener",
            event_type="listener.start",
            ts="2026-01-01T00:00:09Z",
            payload={},
        )
    )

    input_path = tmp_path / "input_spine.jsonl"
    out_a = tmp_path / "out_a.jsonl"
    out_b = tmp_path / "out_b.jsonl"
    _write_jsonl(input_path, input_events)

    run_replay(
        input_spine=input_path,
        output_spine=out_a,
        authority_state=AuthorityState(
            level=AuthorityLevel.V0_2,
            promoted_at="2026-01-01T00:00:00Z",
        ),
    )
    run_replay(
        input_spine=input_path,
        output_spine=out_b,
        authority_state=AuthorityState(
            level=AuthorityLevel.V0_2,
            promoted_at="2026-01-01T00:00:00Z",
        ),
    )

    assert out_a.read_bytes() == out_b.read_bytes()

    emitted = _read_jsonl(out_a)
    listener_events = [
        row
        for row in emitted
        if row.get("source_event_id") == "evt_listener"
        and isinstance(row.get("payload"), dict)
        and isinstance(row["payload"].get("symbol"), str)
    ]

    emitted_symbols = [row["payload"]["symbol"] for row in listener_events]
    assert len(emitted_symbols) == len(symbols)
    assert emitted_symbols == sorted(symbols)

    counts = Counter(emitted_symbols)
    for symbol in symbols:
        assert counts[symbol] <= 1
