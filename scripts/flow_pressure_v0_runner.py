#!/usr/bin/env python3
"""
Flow Pressure v0 Runner

DOCTRINE: FLOW_PRESSURE_V0
STATUS: DESCRIPTIVE_ONLY

Modes:
  --mode replay  : read a fixed JSONL file, emit all events, exit
  --mode live    : tail today's trade_prints.jsonl, rotate at midnight UTC

Input:  trade_prints_v0 JSONL (market.trade events)
Output: /root/synthdesk-router/soak_artifacts/flow_pressure_v0/flow_pressure_v0.jsonl

Design follows flow_imbalance_observer_v0.py pattern:
  - Tail with poll
  - Day rotation
  - Signal-safe shutdown
  - Heartbeat logging
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add packages to path

from observer.flow.flow_pressure_v0 import FlowPressureV0
from router.utils.disk import DiskGuard
from synthdesk_spine.event_types import OBSERVER_FLOW_PRESSURE_V0

_disk_guard = DiskGuard()

VERSION = "0.1.0"
DEFAULT_TRADE_PRINTS_DIR = "/root/synthdesk-listener/runs/trade_prints_v0"
DEFAULT_OUTPUT_DIR = "/root/synthdesk-router/soak_artifacts/flow_pressure_v0"
TAIL_POLL_S = 0.25
HEARTBEAT_S = 60


def parse_iso_to_ms(ts: str) -> float:
    """ISO 8601 string -> epoch milliseconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.timestamp() * 1000.0


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_trade(line: str):
    """Parse one trade_prints JSONL line. Returns (symbol, ts_ms, price, qty, side) or None."""
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    payload = ev.get("payload", {})
    symbol = payload.get("symbol") or ev.get("symbol")
    side_str = payload.get("side")
    qty = payload.get("qty")
    price = payload.get("price")
    source_ts = ev.get("source_ts")

    if not symbol or not side_str or qty is None or price is None:
        return None

    try:
        qty_f = float(qty)
        price_f = float(price)
    except (ValueError, TypeError):
        return None

    ts_is_fallback = False
    if source_ts:
        try:
            ts_ms = parse_iso_to_ms(source_ts)
        except (ValueError, TypeError):
            ts_ms = time.time() * 1000.0
            ts_is_fallback = True
    else:
        ts_ms = time.time() * 1000.0
        ts_is_fallback = True

    side = 1 if side_str == "buy" else -1

    return symbol, ts_ms, price_f, qty_f, side, ts_is_fallback


def write_event(out_path: Path, payload: dict) -> None:
    """Append one event to the output JSONL."""
    if _disk_guard.should_skip():
        return
    event = {
        "event_type": OBSERVER_FLOW_PRESSURE_V0,
        "schema_version": "1.0",
        "version": VERSION,
        "obs_ts": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    with open(out_path, "a") as f:
        f.write(json.dumps(event) + "\n")


def run_replay(input_path: Path, output_path: Path, symbol_filter: str | None, logger: logging.Logger):
    """Replay mode: process a fixed file start-to-finish."""
    engine = FlowPressureV0()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Truncate output for clean replay
    if output_path.exists():
        output_path.unlink()

    total = 0
    emitted = 0

    with open(input_path, "r") as f:
        for line in f:
            if not line.strip():
                continue
            parsed = parse_trade(line)
            if parsed is None:
                continue

            symbol, ts_ms, price, qty, side, ts_fallback = parsed
            if symbol_filter and symbol != symbol_filter:
                continue

            payload = engine.process_trade(
                symbol=symbol,
                source_ts_ms=ts_ms,
                price=price,
                qty=qty,
                side=side,
                ts_is_fallback=ts_fallback,
            )
            write_event(output_path, payload)
            total += 1
            emitted += 1

            if total % 50000 == 0:
                logger.info(f"replay progress: {total} trades processed")

    logger.info(f"REPLAY DONE trades={total} emitted={emitted} output={output_path}")


def run_live(trade_prints_dir: Path, output_dir: Path, logger: logging.Logger):
    """Live mode: tail today's trade prints, rotate at midnight."""
    engine = FlowPressureV0()
    output_dir.mkdir(parents=True, exist_ok=True)

    current_day = ""
    fp = None
    file_offset = 0
    total_trades = 0
    total_emits = 0
    last_heartbeat = time.time()

    shutdown = False

    def handle_sig(signum, _frame):
        nonlocal shutdown
        logger.info(f"Signal {signum}, shutting down")
        shutdown = True

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    while not shutdown:
        day = today_str()
        if day != current_day:
            current_day = day
            if fp:
                fp.close()
                fp = None
            file_offset = 0
            logger.info(f"Day rotation: {day}")

        trade_file = trade_prints_dir / day / "trade_prints.jsonl"
        if not trade_file.exists():
            time.sleep(TAIL_POLL_S * 4)
            continue

        if fp is None:
            fp = open(trade_file, "r")
            fp.seek(file_offset)

        # Truncation detection: if file was truncated (copytruncate), reset to beginning
        try:
            current_size = trade_file.stat().st_size
            if file_offset > current_size:
                logger.warning(f"TRUNCATION_DETECTED file_offset={file_offset} size={current_size}, resetting to 0")
                fp.seek(0)
                file_offset = 0
        except OSError:
            pass

        lines_read = 0
        out_path = output_dir / "flow_pressure_v0.jsonl"

        while True:
            line = fp.readline()
            if not line:
                break
            if not line.strip():
                continue
            parsed = parse_trade(line)
            if parsed is None:
                continue

            symbol, ts_ms, price, qty, side, ts_fallback = parsed

            payload = engine.process_trade(
                symbol=symbol,
                source_ts_ms=ts_ms,
                price=price,
                qty=qty,
                side=side,
                ts_is_fallback=ts_fallback,
            )
            write_event(out_path, payload)
            total_trades += 1
            total_emits += 1
            lines_read += 1

        file_offset = fp.tell()

        if time.time() - last_heartbeat >= HEARTBEAT_S:
            logger.info(
                f"HEARTBEAT trades={total_trades} emits={total_emits} day={current_day}"
            )
            last_heartbeat = time.time()

        if lines_read == 0:
            time.sleep(TAIL_POLL_S)

    if fp:
        fp.close()
    logger.info(f"SHUTDOWN trades={total_trades} emits={total_emits}")


def main():
    parser = argparse.ArgumentParser(description="Flow Pressure v0 Runner (DOCTRINE: FLOW_PRESSURE_V0)")
    parser.add_argument("--mode", choices=["replay", "live"], required=True)
    parser.add_argument("--input", type=Path, help="Input JSONL (replay mode)")
    parser.add_argument("--output", type=Path, help="Output JSONL path (replay mode)")
    parser.add_argument("--symbol", type=str, default=None, help="Filter symbol (replay mode)")
    parser.add_argument(
        "--trade-prints-dir", type=Path,
        default=Path(DEFAULT_TRADE_PRINTS_DIR),
        help="Trade prints base dir (live mode)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help="Output dir (live mode)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logger = logging.getLogger("flow-pressure-v0")
    logger.info(f"START version={VERSION} mode={args.mode}")

    if args.mode == "replay":
        if not args.input:
            parser.error("--input required for replay mode")
        output = args.output or (Path(DEFAULT_OUTPUT_DIR) / "flow_pressure_v0.jsonl")
        run_replay(args.input, output, args.symbol, logger)
    else:
        run_live(args.trade_prints_dir, args.output_dir, logger)


if __name__ == "__main__":
    main()
