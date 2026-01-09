#!/usr/bin/env python3
"""
Router v0.1 main runtime.

Deterministic intent synthesizer:
1. Consume facts (from event spine)
2. Apply constraints (beliefs, vetoes, invariants)
3. Synthesize intent (posture, not action)
4. Emit intent events (to spine)

The router is not smart. The router is authoritative.
"""

import argparse
import sys
from pathlib import Path

from router.constraints import VetoReason, evaluate_constraints, should_emit_intent
from router.emit import emit_intent, emit_veto
from router.spine_reader import SpineReader
from router.state import RouterState

# Default spine path (configurable via CLI)
DEFAULT_SPINE_PATH = Path("/root/synthdesk-listener/runs/0.2.0/event_spine.jsonl")
DEFAULT_POLL_INTERVAL = 1.0

# Event types the router consumes
ALLOWED_EVENT_TYPES = {
    "listener.start",
    "listener.crash",
    "invariant.violation",
    "market.regime",
    "market.regime_change",
}


def run_runtime(spine_path: Path, poll_interval: float = DEFAULT_POLL_INTERVAL) -> None:
    """
    Long-running router runtime.

    Args:
        spine_path: Path to event_spine.jsonl
        poll_interval: Seconds between polls
    """
    state = RouterState()
    reader = SpineReader(spine_path, poll_interval)

    print("router v0.1 runtime started", file=sys.stderr, flush=True)
    print(f"spine: {spine_path}", file=sys.stderr, flush=True)
    print(f"poll: {poll_interval}s", file=sys.stderr, flush=True)

    for event in reader.tail(skip_existing=False):
        event_type = event.get("event_type")
        event_id = event.get("event_id")
        timestamp = event.get("timestamp")
        payload = event.get("payload")

        # Filter: only consume allowed event types
        if event_type not in ALLOWED_EVENT_TYPES:
            continue

        # Update state from event
        state.update_from_event(event)

        # Synthesize intent for symbols affected by this event
        symbols_to_check = set()

        if event_type in ("market.regime", "market.regime_change"):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in ("listener.start", "listener.crash", "invariant.violation"):
            symbols_to_check.update(state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        for symbol in symbols_to_check:
            if not emit_allowed:
                continue

            # Evaluate constraints → intent or veto reason
            state_dict = {
                "system": state.system,
                "symbols": state.symbols,
            }
            result = evaluate_constraints(state_dict, symbol)

            if isinstance(result, VetoReason):
                # Veto path: typed silence
                last_veto = state.get_last_veto_reason(symbol)
                if result.value != last_veto:
                    emit_veto(
                        spine_path=spine_path,
                        symbol=symbol,
                        veto_reason=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    state.set_last_veto_reason(symbol, result.value)
            else:
                # Intent path: positive exposure authority
                last_intent = state.get_last_intent(symbol)
                if should_emit_intent(result, last_intent):
                    emit_intent(
                        spine_path=spine_path,
                        symbol=symbol,
                        intent=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    state.set_last_intent(symbol, result)


def run_replay(input_spine: Path, output_spine: Path) -> None:
    """
    Replay mode (determinism testing).

    Args:
        input_spine: Input event_spine.jsonl
        output_spine: Output router_intent.jsonl
    """
    state = RouterState()
    reader = SpineReader(input_spine)

    print("router v0.1 replay mode", file=sys.stderr, flush=True)
    print(f"input: {input_spine}", file=sys.stderr, flush=True)
    print(f"output: {output_spine}", file=sys.stderr, flush=True)

    for event in reader.replay():
        event_type = event.get("event_type")
        event_id = event.get("event_id")
        timestamp = event.get("timestamp")
        payload = event.get("payload")

        # Filter: only consume allowed event types
        if event_type not in ALLOWED_EVENT_TYPES:
            continue

        # Update state from event
        state.update_from_event(event)

        # Synthesize intent for symbols affected by this event
        symbols_to_check = set()

        if event_type in ("market.regime", "market.regime_change"):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in ("listener.start", "listener.crash", "invariant.violation"):
            symbols_to_check.update(state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        for symbol in symbols_to_check:
            if not emit_allowed:
                continue

            # Evaluate constraints → intent or veto reason
            state_dict = {
                "system": state.system,
                "symbols": state.symbols,
            }
            result = evaluate_constraints(state_dict, symbol)

            if isinstance(result, VetoReason):
                # Veto path: typed silence
                last_veto = state.get_last_veto_reason(symbol)
                if result.value != last_veto:
                    emit_veto(
                        spine_path=output_spine,
                        symbol=symbol,
                        veto_reason=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    state.set_last_veto_reason(symbol, result.value)
            else:
                # Intent path: positive exposure authority
                last_intent = state.get_last_intent(symbol)
                if should_emit_intent(result, last_intent):
                    emit_intent(
                        spine_path=output_spine,
                        symbol=symbol,
                        intent=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    state.set_last_intent(symbol, result)

    print("replay complete", file=sys.stderr, flush=True)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Router v0.1 - Deterministic intent synthesizer")
    parser.add_argument(
        "--replay",
        nargs=2,
        metavar=("INPUT_SPINE", "OUTPUT_SPINE"),
        help="Replay mode: process input spine, write intents to output",
    )
    parser.add_argument(
        "--spine",
        type=Path,
        default=DEFAULT_SPINE_PATH,
        help=f"Event spine path (default: {DEFAULT_SPINE_PATH})",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )

    args = parser.parse_args()

    if args.replay:
        input_spine = Path(args.replay[0])
        output_spine = Path(args.replay[1])
        run_replay(input_spine, output_spine)
    else:
        run_runtime(args.spine, args.poll)


if __name__ == "__main__":
    main()
