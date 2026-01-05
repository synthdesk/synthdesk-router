"""
Intent emission - Write router.intent events to spine.

Constitutional exhaust port.
"""

import json
from pathlib import Path
from typing import Dict

# Canonical float handling (FPDET-1)
try:
    from synthdesk_spine import canonicalize_payload
except ImportError:
    # Fallback if spine SDK not installed (legacy mode)
    def canonicalize_payload(payload: Dict, **kwargs) -> Dict:
        return payload


def emit_intent(
    spine_path: Path,
    symbol: str,
    intent: Dict,
    source_event_id: str,
    source_ts: str,
) -> None:
    """
    Append router.intent event to spine.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        intent: Intent dict (direction, size_pct, risk_cap, rationale)
        source_event_id: Event ID that triggered this intent
        source_ts: Timestamp from source event
    """
    # Canonicalize payload at emission boundary (FPDET-1)
    payload = {
        "symbol": symbol,
        "direction": intent["direction"],
        "size_pct": intent["size_pct"],
        "risk_cap": intent["risk_cap"],
        "rationale": intent["rationale"],
    }
    payload = canonicalize_payload(payload, skip_unknown=True)

    event = {
        "event_type": "router.intent",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    try:
        with spine_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
    except OSError:
        # Silent failure - no crash propagation
        pass
