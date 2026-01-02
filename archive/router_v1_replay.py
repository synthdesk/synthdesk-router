#!/usr/bin/env python3
"""
Router v1 replay engine.

Deterministic.
Replay-only.
No policy yet.
"""

raise RuntimeError("archived: non-runnable, non-authoritative")

import json
import sys
from pathlib import Path
from typing import Dict, Optional

from synthdesk_router.io.intent_writer import append_intent
from synthdesk_router.schemas.router_intent import validate_router_intent


def _intent_for_regime(regime: str) -> Optional[Dict[str, object]]:
    if regime == "high_vol":
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["regime=high_vol"],
        }
    if regime == "chop":
        return {
            "direction": "flat",
            "size_pct": 0.0,
            "risk_cap": "low",
            "rationale": ["regime=chop"],
        }
    if regime == "drift":
        return {
            "direction": "long",
            "size_pct": 0.25,
            "risk_cap": "normal",
            "rationale": ["regime=drift"],
        }
    if regime == "breakout":
        return {
            "direction": "long",
            "size_pct": 0.25,
            "risk_cap": "high",
            "rationale": ["regime=breakout"],
        }
    return None


def replay(event_spine: Path, intent_log: Path) -> None:
    last_intent_by_symbol: Dict[str, Dict[str, object]] = {}

    with event_spine.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue

            event = json.loads(line)
            event_type = event.get("event_type")
            payload = event.get("payload")
            event_id = event.get("event_id")
            event_ts = event.get("timestamp")

            if not isinstance(event_type, str) or not isinstance(payload, dict):
                continue

            intent = None
            symbol = None
            if event_type == "invariant.violation":
                if not isinstance(event_id, str) or not isinstance(event_ts, str):
                    continue
                intent = {
                    "direction": "flat",
                    "size_pct": 0.0,
                    "risk_cap": "low",
                    "rationale": ["invariant.violation observed"],
                }
            elif event_type == "market.regime":
                symbol = payload.get("symbol")
                regime = payload.get("regime")
                if (
                    isinstance(symbol, str)
                    and isinstance(regime, str)
                    and isinstance(event_id, str)
                    and isinstance(event_ts, str)
                ):
                    intent = _intent_for_regime(regime)
            elif event_type == "market.regime_change":
                symbol = payload.get("symbol")
                if (
                    isinstance(symbol, str)
                    and isinstance(event_id, str)
                    and isinstance(event_ts, str)
                ):
                    prior_intent = last_intent_by_symbol.get(symbol)
                    if isinstance(prior_intent, dict):
                        prior_rationale = prior_intent.get("rationale")
                        rationale = list(prior_rationale) if isinstance(prior_rationale, list) else []
                        from_regime = payload.get("from")
                        to_regime = payload.get("to")
                        if isinstance(from_regime, str) and isinstance(to_regime, str):
                            note = f"regime_change {from_regime}->{to_regime}"
                        else:
                            note = "regime_change observed"
                        rationale.append(note)
                        intent = {
                            "direction": prior_intent.get("direction"),
                            "size_pct": prior_intent.get("size_pct"),
                            "risk_cap": prior_intent.get("risk_cap"),
                            "rationale": rationale,
                        }

            if intent is None:
                continue
            if symbol is not None and intent == last_intent_by_symbol.get(symbol):
                continue
            validate_router_intent(intent)
            append_intent(intent_log, intent, source_event_id=event_id, source_ts=event_ts)
            if symbol is not None:
                last_intent_by_symbol[symbol] = intent


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: router_v1_replay.py event_spine.jsonl intent_log.jsonl")
        sys.exit(1)

    replay(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
