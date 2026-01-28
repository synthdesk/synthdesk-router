#!/usr/bin/env python3
"""
Regime Surface Emitter v0.1

Converts market.regime events from spine into risk.veto_surface.regime.v1 events.

Contract:
- Input: market.regime events from spine jsonl
- Output: append risk.veto_surface.regime.v1 to regime.jsonl
- Cadence: event-driven (tail/follow spine)
- Idempotent: dedup by source_event_id

Status mapping:
- No recent market.regime for asset → NO_DATA (not emitted)
- Last event age > stale_after_s → STALE
- Else → ACTIVE

DOCTRINE: VETO_TIMESCALE
Created: 2026-01-23
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

# Add packages to path for spine_sdk import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from synthdesk_spine.event_types import RISK_VETO_SURFACE_REGIME_V1


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(ts: str) -> float:
    """Parse ISO timestamp to unix timestamp."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).timestamp()


def compute_event_hash(event: Dict) -> str:
    """Compute deterministic hash for event."""
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_dedup(path: str, max_ids: int = 200000) -> Set[str]:
    """Load seen event IDs from dedup file."""
    if not os.path.exists(path):
        return set()
    ids = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.add(line)
                if len(ids) >= max_ids:
                    break
    except OSError:
        pass
    return ids


def append_dedup(path: str, event_id: str) -> None:
    """Append event ID to dedup file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(event_id + "\n")


def emit(out_path: str, obj: Dict) -> None:
    """Append event to output jsonl file."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n")
        f.flush()


def follow_file(path: str, poll_s: float):
    """Tail file, yielding new lines."""
    with open(path, "r", encoding="utf-8") as f:
        # Seek to end
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(poll_s)
                continue
            yield line


def extract_event_id(evt: Dict, raw: str) -> str:
    """Extract or compute event ID."""
    # Try standard fields
    for field in ["event_id", "id", "source_event_id"]:
        if evt.get(field):
            return evt[field]
    # Fallback: hash the raw line
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def extract_asset(payload: Dict) -> Optional[str]:
    """Extract asset symbol from payload."""
    for field in ["symbol", "asset", "pair"]:
        if payload.get(field):
            return payload[field]
    return None


def main():
    ap = argparse.ArgumentParser(description="Regime Surface Emitter v0.1")
    ap.add_argument("--spine", required=True, help="Path to event spine jsonl")
    ap.add_argument("--out", required=True, help="Output path for regime.jsonl")
    ap.add_argument("--turn-on-min", type=int, default=15,
                    help="Default turn_on_min threshold (minutes)")
    ap.add_argument("--stale-after-s", type=int, default=180,
                    help="Seconds after which regime is considered stale")
    ap.add_argument("--poll", type=float, default=0.25,
                    help="Poll interval in seconds")
    ap.add_argument("--dedup",
                    default="/root/synthdesk-router/soak_artifacts/veto_surfaces/regime_emitter_dedup.txt",
                    help="Path to dedup file")
    ap.add_argument("--asset-filter", default="",
                    help="Comma-separated list of assets to include (empty = all)")
    args = ap.parse_args()

    # Parse asset filter
    asset_allow = None
    if args.asset_filter:
        asset_allow = set(a.strip() for a in args.asset_filter.split(",") if a.strip())

    # Load dedup state
    seen = load_dedup(args.dedup)
    print(f"[regime_surface_emitter] Loaded {len(seen)} seen event IDs", flush=True)
    print(f"[regime_surface_emitter] Tailing {args.spine}", flush=True)
    print(f"[regime_surface_emitter] Output: {args.out}", flush=True)
    print(f"[regime_surface_emitter] turn_on_min={args.turn_on_min}, stale_after_s={args.stale_after_s}", flush=True)

    # Track last regime per asset (for staleness)
    last_by_asset: Dict[str, Tuple[str, str, str]] = {}  # asset -> (source_ts, event_id, label)
    emitted_count = 0

    for raw in follow_file(args.spine, args.poll):
        raw = raw.strip()
        if not raw:
            continue

        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Filter to market.regime events only
        if evt.get("event_type") != "market.regime":
            continue

        payload = evt.get("payload") or {}
        asset = extract_asset(payload)
        if not asset:
            continue

        # Apply asset filter if specified
        if asset_allow is not None and asset not in asset_allow:
            continue

        # Extract event ID for dedup
        source_event_id = extract_event_id(evt, raw)
        if source_event_id in seen:
            continue

        # Extract fields
        source_ts = evt.get("ts") or payload.get("ts") or utc_now_iso()
        regime_label = (
            payload.get("regime") or
            payload.get("label") or
            payload.get("regime_label") or
            "unknown"
        )

        # Update tracking
        last_by_asset[asset] = (source_ts, source_event_id, regime_label)

        # Determine status based on age
        try:
            age_s = time.time() - parse_ts(source_ts)
        except (ValueError, TypeError):
            age_s = float("inf")

        status = "ACTIVE" if age_s <= args.stale_after_s else "STALE"

        # Build output event
        out_evt = {
            "event_type": RISK_VETO_SURFACE_REGIME_V1,
            "ts": utc_now_iso(),
            "payload": {
                "asset": asset,
                "plane": "regime",
                "schema_version": "1.0",
                "status": status,
                "horizons_min": [5, 15, 60, 240],
                "turn_on_min": args.turn_on_min,
                "unsafe_up_to_min": None,
                "risk_by_horizon": {},
                "reasons": {
                    "regime": [f"regime={regime_label}"],
                    "micro": [],
                },
                "inputs": {
                    "evidence_refs": [source_event_id],
                    "input_hash": compute_event_hash({"source": source_event_id}),
                    "params_hash": compute_event_hash({"turn_on_min": args.turn_on_min}),
                    "code_sha": "regime_emitter_v0.1",
                },
                "integrity": {
                    "event_hash": compute_event_hash(evt),
                    "prev_event_hash": None,
                },
                "regime_label": regime_label,
                "source_event_id": source_event_id,
                "source_ts": source_ts,
            },
        }

        # Emit
        emit(args.out, out_evt)
        emitted_count += 1

        # Update dedup
        seen.add(source_event_id)
        append_dedup(args.dedup, source_event_id)

        if emitted_count % 100 == 0:
            print(f"[regime_surface_emitter] Emitted {emitted_count} surfaces", flush=True)


if __name__ == "__main__":
    main()
