#!/usr/bin/env python3
"""
Hostility Veto Soak v0

DOCTRINE: LIQUIDITY_HOSTILITY_V0 (veto evaluation)
STATUS: SHADOW_ONLY — does not modify spine, does not block execution

Purpose:
  Shadow-evaluate whether router intents would have been vetoed by flow hostility.
  Measures the hypothetical effect of the veto rule:
    "if hostility != quiet -> block quote placement"

Method:
  1. Tail liquidity_hostility_v0 artifact for current regime per symbol
  2. Tail router spine for router.intent events
  3. For each intent, record: would it have been vetoed? What was the regime?
  4. Emit shadow audit events to artifact

Output metrics (accumulated over time):
  - intents_total: how many intents arrived
  - intents_vetoed: how many would have been blocked
  - veto_rate: vetoed / total
  - regime_at_intent: distribution of regimes when intents fire
  - For vetoed intents: track subsequent price move (did veto save money?)

This is a measurement harness. It changes nothing. It tells you what WOULD happen.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "0.1.0"
TAIL_POLL_S = 0.5
HEARTBEAT_S = 60
SUMMARY_S = 300  # Summary every 5 minutes


def main():
    parser = argparse.ArgumentParser(description="Hostility Veto Soak v0 (SHADOW_ONLY)")
    parser.add_argument(
        "--hostility-input", type=Path,
        default=Path("/root/synthdesk-router/soak_artifacts/liquidity_hostility_v0/liquidity_hostility_v0.jsonl"),
    )
    parser.add_argument(
        "--spine-input", type=Path,
        default=Path("/var/lib/synthdesk/spine/router_spine.jsonl"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("/root/synthdesk-router/soak_artifacts/hostility_veto_soak_v0"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logger = logging.getLogger("hostility-veto-soak")
    logger.info(f"START version={VERSION}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "hostility_veto_soak_v0.jsonl"

    # Current regime state per symbol (updated from hostility stream)
    current_regime: dict[str, str] = {}
    current_cooldown: dict[str, bool] = {}

    # Counters
    intents_total = 0
    intents_vetoed = 0
    regime_at_intent: Counter = Counter()

    # File positions
    hostility_fp = None
    hostility_offset = 0
    spine_fp = None
    spine_offset = 0

    shutdown = False

    def handle_sig(signum, _):
        nonlocal shutdown
        logger.info(f"Signal {signum}, shutting down")
        shutdown = True

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    last_heartbeat = time.time()
    last_summary = time.time()

    # Read both files from beginning to build state and process historical intents
    # Hostility file must be read first to build regime state before evaluating intents
    logger.info(f"Hostility: {args.hostility_input} (reading from start)")
    logger.info(f"Spine: {args.spine_input} (reading from start)")

    while not shutdown:
        # 1. Read new hostility events
        if args.hostility_input.exists():
            if hostility_fp is None:
                hostility_fp = open(args.hostility_input, "r")
                hostility_fp.seek(hostility_offset)

            for line in hostility_fp:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    pl = ev.get("payload", {})
                    symbol = pl.get("symbol")
                    regime = pl.get("regime")
                    cooldown = pl.get("cooldown_active", False)
                    if symbol and regime:
                        current_regime[symbol] = regime
                        current_cooldown[symbol] = cooldown
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue

            hostility_offset = hostility_fp.tell()

        # 2. Read new spine events
        if args.spine_input.exists():
            if spine_fp is None:
                spine_fp = open(args.spine_input, "r")
                spine_fp.seek(spine_offset)

            for line in spine_fp:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                event_type = ev.get("event_type", "")
                # Catch all intent variants: router.intent, router.intent_weak, router.intent_shadow
                if not event_type.startswith("router.intent"):
                    continue

                payload = ev.get("payload", {})
                symbol = payload.get("symbol", "UNKNOWN")
                regime = current_regime.get(symbol, "unknown")
                cooldown = current_cooldown.get(symbol, False)

                # Veto rule: block if hostility != quiet
                would_veto = regime in ("pressured", "swept")

                intents_total += 1
                if would_veto:
                    intents_vetoed += 1
                regime_at_intent[regime] += 1

                direction = payload.get("direction", "unknown")
                intent_strength = payload.get("strength", event_type.split("_")[-1] if "_" in event_type else "standard")
                # Preserve the original intent source_ts for price alignment in audits
                intent_source_ts = payload.get("source_ts") or ev.get("source_ts") or ev.get("timestamp")

                # Write shadow audit event
                audit_event = {
                    "event_type": "shadow.hostility_veto_v0",
                    "version": VERSION,
                    "obs_ts": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "symbol": symbol,
                        "intent_source_ts": intent_source_ts,
                        "intent_event_type": event_type,
                        "intent_direction": direction,
                        "intent_strength": intent_strength,
                        "regime_at_intent": regime,
                        "cooldown_active": cooldown,
                        "would_veto": would_veto,
                        "veto_reason": f"hostility={regime}" if would_veto else None,
                        "cumulative": {
                            "intents_total": intents_total,
                            "intents_vetoed": intents_vetoed,
                            "veto_rate": round(intents_vetoed / max(intents_total, 1), 4),
                        },
                    },
                }
                with open(out_path, "a") as f:
                    f.write(json.dumps(audit_event) + "\n")

            spine_offset = spine_fp.tell()

        # Heartbeat
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_S:
            regimes_str = ", ".join(f"{s}={r}" for s, r in current_regime.items()) or "none"
            logger.info(
                f"HEARTBEAT intents={intents_total} vetoed={intents_vetoed} "
                f"regimes=[{regimes_str}]"
            )
            last_heartbeat = now

        # Periodic summary
        if now - last_summary >= SUMMARY_S and intents_total > 0:
            veto_rate = intents_vetoed / intents_total
            logger.info(
                f"SUMMARY intents={intents_total} vetoed={intents_vetoed} "
                f"veto_rate={veto_rate:.1%} regime_dist={dict(regime_at_intent)}"
            )
            last_summary = now

        time.sleep(TAIL_POLL_S)

    # Cleanup
    if hostility_fp:
        hostility_fp.close()
    if spine_fp:
        spine_fp.close()

    logger.info(f"SHUTDOWN intents={intents_total} vetoed={intents_vetoed}")


if __name__ == "__main__":
    main()
