#!/usr/bin/env python3
"""
Router v0.2 main runtime.

Deterministic intent synthesizer with authority tiers:
1. Consume facts (from event spine)
2. Bind authority (from promotion certificate)
3. Apply constraints (beliefs, vetoes, invariants)
4. Synthesize intent (posture, not action)
5. Emit intent events (to spine) - GATED BY AUTHORITY LEVEL

The router is not smart. The router is authoritative.
Authority is not assumed. Authority is proven via certificate.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from router.allocator import AllocationResult, Direction
from router.authority import (
    AuthorityLevel,
    AuthorityState,
    DemotionWatcher,
    bind_authority,
    create_severity_gated_violation_check,
)
from router.constraints import (
    IntentStrength,
    VetoReason,
    evaluate_constraints_with_strength,
    should_emit_intent,
)
from router.emit import emit_intent, emit_shadow_intent, emit_veto, emit_weak_intent, emit_heartbeat, set_router_build_sha, set_soak_contract_hash
from router.envelope_provider import TickBuffer
from router.spine_reader import SpineReader
from router.state import RouterState

# Version
ROUTER_VERSION = "0.2"

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

# Critical source files for build metadata
# All governance-critical modules must be included here
# Changes to any of these files invalidate promotion certificates
CRITICAL_SOURCE_FILES = [
    # Core runtime
    "packages/router/router/main.py",
    "packages/router/router/constraints.py",
    "packages/router/router/intent.py",
    "packages/router/router/state.py",
    "packages/router/router/emit.py",
    # Epistemic allocator (v0.2)
    "packages/router/router/allocator.py",
    # Authority management
    "packages/router/router/authority.py",
    "packages/router/router/signing.py",
    # Trust root - CRITICAL: changing the verifier key invalidates certs
    "packages/router/router/public_key.b64",
    # Schemas
    "packages/router/schemas/router_intent.py",
]


def get_file_sha256(file_path: Path) -> str:
    """Compute SHA256 of a single file."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_build_metadata(repo_root: Path) -> Dict[str, Any]:
    """Compute cryptographic hashes of critical source files."""
    file_hashes = {}
    combined = hashlib.sha256()

    for rel_path in sorted(CRITICAL_SOURCE_FILES):
        file_path = repo_root / rel_path
        if file_path.exists():
            file_hash = get_file_sha256(file_path)
            file_hashes[rel_path] = file_hash
            combined.update(f"{rel_path}:{file_hash}\n".encode("utf-8"))
        else:
            file_hashes[rel_path] = "NOT_FOUND"
            combined.update(f"{rel_path}:NOT_FOUND\n".encode("utf-8"))

    return {
        "source_files": file_hashes,
        "combined_sha256": combined.hexdigest(),
        "critical_files": CRITICAL_SOURCE_FILES,
    }


def get_router_commit(repo_root: Path) -> str:
    """Get router package git commit hash."""
    router_pkg = repo_root / "packages" / "router"
    try:
        result = subprocess.run(
            ["git", "-C", str(router_pkg), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _parse_tick_timestamp(ts_str: str) -> Optional[float]:
    """
    Parse tick timestamp to Unix epoch.

    Handles ISO format with timezone (e.g., "2026-01-12T00:00:09.345468+00:00").
    """
    try:
        # Try ISO format with timezone
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


class TickWatcher:
    """
    Watch tick observation files and maintain rolling buffer per symbol.

    CRITICAL: Stores tick timestamps to enforce tick_ts <= event_ts constraint.
    This prevents future data leakage when router replays historical events.

    Tick files are daily: runs/0.2.0/YYYY-MM-DD/tick_observation.jsonl
    """

    def __init__(self, runs_dir: Path, tick_buffer: TickBuffer):
        """
        Initialize tick watcher.

        Args:
            runs_dir: Path to runs/0.2.0 directory
            tick_buffer: Buffer to populate with ticks (must store timestamps)
        """
        self.runs_dir = runs_dir
        self.tick_buffer = tick_buffer
        self._last_positions: Dict[Path, int] = {}

    def _get_today_tick_file(self) -> Optional[Path]:
        """Get path to today's tick file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tick_file = self.runs_dir / today / "tick_observation.jsonl"
        if tick_file.exists():
            return tick_file
        return None

    def poll_ticks(self) -> int:
        """
        Poll for new ticks and update buffer.

        Returns:
            Number of new ticks consumed
        """
        tick_file = self._get_today_tick_file()
        if tick_file is None:
            return 0

        last_pos = self._last_positions.get(tick_file, 0)
        count = 0

        try:
            with tick_file.open("r", encoding="utf-8") as f:
                f.seek(last_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        tick = json.loads(line)
                        symbol = tick.get("asset")
                        price = tick.get("price")
                        ts_str = tick.get("ts_utc")

                        # CRITICAL: Must have timestamp for asof filtering
                        if not isinstance(ts_str, str):
                            continue
                        ts_epoch = _parse_tick_timestamp(ts_str)
                        if ts_epoch is None:
                            continue

                        if isinstance(symbol, str) and isinstance(price, (int, float)):
                            self.tick_buffer.add_tick(symbol, ts_epoch, float(price))
                            count += 1
                    except json.JSONDecodeError:
                        continue
                self._last_positions[tick_file] = f.tell()
        except OSError:
            pass

        return count


def emit_demotion_event(
    spine_path: Path,
    demotions_path: Optional[Path],
    from_level: AuthorityLevel,
    to_level: AuthorityLevel,
    trigger: str,
    cert_body_sha256: Optional[str] = None,
    build_meta_sha256: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """
    Emit durable demotion event to BOTH spine AND sidecar.

    Per contract: demotions must be recorded in spine for audit trail.
    Sidecar (authority_demotions.jsonl) is kept for backward compatibility.

    Args:
        spine_path: Path to event_spine.jsonl (REQUIRED)
        demotions_path: Path to sidecar file (optional)
        from_level: Authority level before demotion
        to_level: Authority level after demotion
        trigger: What triggered the demotion
        cert_body_sha256: Certificate digest for audit
        build_meta_sha256: Build metadata digest for audit
        details: Additional context
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # Spine event (canonical)
    spine_event = {
        "event_type": "router.authority_demotion",
        "timestamp": timestamp,
        "payload": {
            "from_level": str(from_level),
            "to_level": str(to_level),
            "trigger": trigger,
            "cert_body_sha256": cert_body_sha256,
            "build_meta_sha256": build_meta_sha256,
            "details": details,
        },
    }
    try:
        with spine_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(spine_event, sort_keys=True) + "\n")
            f.flush()
    except OSError:
        print(f"WARNING: Failed to write demotion event to spine {spine_path}", file=sys.stderr)

    # Sidecar event (backward compatibility)
    if demotions_path:
        sidecar_event = {
            "event_type": "authority.demotion",
            "timestamp": timestamp,
            "from_level": str(from_level),
            "to_level": str(to_level),
            "trigger": trigger,
            "cert_body_sha256": cert_body_sha256,
            "build_meta_sha256": build_meta_sha256,
            "details": details,
        }
        try:
            with demotions_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(sidecar_event, sort_keys=True) + "\n")
                f.flush()
        except OSError:
            print(f"WARNING: Failed to write demotion event to sidecar {demotions_path}", file=sys.stderr)


class AuthorityGatedRouter:
    """
    Router with authority gating.

    Non-flat emissions are BLOCKED unless authority level permits.
    """

    def __init__(
        self,
        authority_state: AuthorityState,
        router_state: RouterState,
        spine_path: Path,
        demotions_path: Optional[Path] = None,
    ):
        self.authority_state = authority_state
        self.router_state = router_state
        self.spine_path = spine_path
        self.demotions_path = demotions_path

        # Setup demotion watcher with severity-gated violation check
        # Only critical violations trigger authority demotion; warnings degrade posture only
        self.demotion_watcher = DemotionWatcher(authority_state)
        self.demotion_watcher.add_check(
            create_severity_gated_violation_check(router_state.get_last_violation)
        )

        # Track if we've already emitted demotion for current session
        self._demotion_emitted = False

    def check_demotion(self) -> None:
        """Check demotion triggers and emit durable event if demoted."""
        old_level = self.authority_state.level
        self.demotion_watcher.check_all()
        new_level = self.authority_state.level

        if old_level != new_level and not self._demotion_emitted:
            if self.authority_state.demotions:
                latest = self.authority_state.demotions[-1]
                emit_demotion_event(
                    spine_path=self.spine_path,
                    demotions_path=self.demotions_path,
                    from_level=latest.from_level,
                    to_level=latest.to_level,
                    trigger=latest.trigger,
                    cert_body_sha256=self.authority_state.cert_body_sha256,
                    build_meta_sha256=self.authority_state.build_meta_sha256,
                    details=latest.details,
                )
                self._demotion_emitted = True

    def can_emit_non_flat(self) -> bool:
        """Check if non-flat emissions are permitted."""
        return self.authority_state.level.can_emit_non_flat

    def get_authority_level(self) -> AuthorityLevel:
        """Get current authority level."""
        return self.authority_state.level


def run_runtime(
    spine_path: Path,
    poll_interval: float,
    authority_state: AuthorityState,
    demotions_path: Optional[Path] = None,
    use_real_kernel: bool = True,
    heartbeat_interval: float = 30.0,
) -> None:
    """
    Long-running router runtime with authority gating.

    Args:
        spine_path: Path to event_spine.jsonl
        poll_interval: Seconds between polls
        authority_state: Bound authority state
        demotions_path: Path for durable demotion recording
        use_real_kernel: If True, use bootstrap MC kernel (requires tick data)
        heartbeat_interval: Seconds between heartbeat emissions
    """
    # Pass promoted_at as authority epoch: violations before this timestamp
    # cannot demote the current authority binding (they belong to a prior epoch)
    router_state = RouterState(authority_epoch_ts=authority_state.promoted_at)
    reader = SpineReader(spine_path, poll_interval)
    gated_router = AuthorityGatedRouter(authority_state, router_state, spine_path, demotions_path)

    # Setup tick buffer and watcher for real MC kernel
    tick_buffer = TickBuffer(max_size=1200)  # ~20 minutes at 1 tick/sec
    runs_dir = spine_path.parent  # Assume spine is in runs/0.2.0/
    tick_watcher = TickWatcher(runs_dir, tick_buffer)

    # Heartbeat state
    last_heartbeat_time = time.time()
    events_consumed = 0
    intents_emitted = 0
    vetoes_emitted = 0
    shadows_emitted = 0
    last_event_ts: Optional[str] = None

    print(f"router v{ROUTER_VERSION} runtime started", file=sys.stderr, flush=True)
    print(f"authority_level: {authority_state.level}", file=sys.stderr, flush=True)
    print(f"spine: {spine_path}", file=sys.stderr, flush=True)
    print(f"poll: {poll_interval}s", file=sys.stderr, flush=True)
    print(f"real_kernel: {use_real_kernel}", file=sys.stderr, flush=True)
    print(f"heartbeat_interval: {heartbeat_interval}s", file=sys.stderr, flush=True)

    for event in reader.tail_with_heartbeat(skip_existing=False):
        # Check if heartbeat is due
        now = time.time()
        if now - last_heartbeat_time >= heartbeat_interval:
            emit_heartbeat(
                spine_path=spine_path,
                authority_level=str(authority_state.level),
                spine_offset=reader.offset,
                events_consumed=events_consumed,
                intents_emitted=intents_emitted,
                vetoes_emitted=vetoes_emitted,
                shadows_emitted=shadows_emitted,
                last_event_ts=last_event_ts,
            )
            last_heartbeat_time = now

        # None means poll cycle with no events - continue to next cycle
        if event is None:
            continue

        # Poll ticks on each iteration (cheap if no new data)
        tick_watcher.poll_ticks()

        event_type = event.get("event_type")
        event_id = event.get("event_id")
        timestamp = event.get("timestamp")
        payload = event.get("payload")

        # Filter: only consume allowed event types
        if event_type not in ALLOWED_EVENT_TYPES:
            continue

        # Track event consumption for heartbeat
        events_consumed += 1
        if isinstance(timestamp, str):
            last_event_ts = timestamp

        # Update state from event
        router_state.update_from_event(event)

        # Check demotion triggers after state update
        gated_router.check_demotion()

        # Synthesize intent for symbols affected by this event
        symbols_to_check = set()

        if event_type in ("market.regime", "market.regime_change"):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in ("listener.start", "listener.crash", "invariant.violation"):
            symbols_to_check.update(router_state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        for symbol in symbols_to_check:
            if not emit_allowed:
                continue

            # Evaluate constraints → intent or veto reason, with strength classification
            state_dict = {
                "system": router_state.system,
                "symbols": router_state.symbols,
                "degraded_symbols": router_state.get_degraded_symbols(),
            }
            result, intent_strength = evaluate_constraints_with_strength(state_dict, symbol)

            # Get tick prices for real envelope (if enabled)
            # CRITICAL: Pass asof_ts to enforce tick_ts <= event_ts constraint
            # This prevents future data leakage when replaying historical events
            if use_real_kernel:
                asof_ts = _parse_tick_timestamp(timestamp)
                tick_prices = tick_buffer.get_prices(symbol, asof_ts=asof_ts) if asof_ts else None
            else:
                tick_prices = None

            # Get z_mean/z_std/regime for composite envelope (momentum_v0 direction)
            z_mean = router_state.get_z_mean(symbol)
            z_std = router_state.get_z_std(symbol)
            regime = router_state.get_regime(symbol)
            regime_confidence = router_state.get_regime_confidence(symbol)

            if isinstance(result, VetoReason):
                # Veto path: typed silence (always permitted)
                last_veto = router_state.get_last_veto_reason(symbol)
                if result.value != last_veto:
                    emit_veto(
                        spine_path=spine_path,
                        symbol=symbol,
                        veto_reason=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    vetoes_emitted += 1
                    router_state.set_last_veto_reason(symbol, result.value)
            elif isinstance(result, AllocationResult):
                # Intent path: positive exposure authority (v0.2 allocator)

                # WEAK INTENT PATH: questions, not decisions
                # - NOT gated by authority
                # - Do NOT update state.last_allocation (independent tracking)
                # - Always emit (no dedup against strong intents)
                if intent_strength == IntentStrength.WEAK:
                    emit_weak_intent(
                        spine_path=spine_path,
                        symbol=symbol,
                        allocation=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                        tick_prices=tick_prices,
                        use_real_kernel=use_real_kernel,
                        z_mean=z_mean,
                        z_std=z_std,
                        regime=regime,
                        regime_confidence=regime_confidence,
                    )
                    intents_emitted += 1  # Count weak intents too
                    # NOTE: Do NOT update router_state.set_last_allocation()
                    # Weak intents track independently from strong intents
                    continue

                # STRONG INTENT PATH: decisions
                # GATED: non-flat requires v0.2+ authority
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit shadow intent (counterfactual) + veto
                    # Shadow intent feeds EVT-1 without granting real authority
                    last_allocation = router_state.get_last_allocation(symbol)
                    if should_emit_intent(result, last_allocation):
                        emit_shadow_intent(
                            spine_path=spine_path,
                            symbol=symbol,
                            allocation=result,
                            blocked_by="authority_gate",
                            source_event_id=event_id,
                            source_ts=timestamp,
                            tick_prices=tick_prices,
                            use_real_kernel=use_real_kernel,
                            z_mean=z_mean,
                            z_std=z_std,
                            regime=regime,
                            regime_confidence=regime_confidence,
                        )
                        shadows_emitted += 1
                        router_state.set_last_allocation(symbol, result)

                    last_veto = router_state.get_last_veto_reason(symbol)
                    if VetoReason.AUTHORITY_GATE.value != last_veto:
                        emit_veto(
                            spine_path=spine_path,
                            symbol=symbol,
                            veto_reason=VetoReason.AUTHORITY_GATE,
                            source_event_id=event_id,
                            source_ts=timestamp,
                        )
                        vetoes_emitted += 1
                        router_state.set_last_veto_reason(symbol, VetoReason.AUTHORITY_GATE.value)
                    continue  # Skip real intent emission

                last_allocation = router_state.get_last_allocation(symbol)
                if should_emit_intent(result, last_allocation):
                    emit_intent(
                        spine_path=spine_path,
                        symbol=symbol,
                        allocation=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                        tick_prices=tick_prices,
                        use_real_kernel=use_real_kernel,
                        z_mean=z_mean,
                        z_std=z_std,
                        regime=regime,
                        regime_confidence=regime_confidence,
                    )
                    intents_emitted += 1
                    router_state.set_last_allocation(symbol, result)


def run_replay(
    input_spine: Path,
    output_spine: Path,
    authority_state: AuthorityState,
) -> None:
    """
    Replay mode (determinism testing) with authority gating.

    Args:
        input_spine: Input event_spine.jsonl
        output_spine: Output router_intent.jsonl
        authority_state: Bound authority state
    """
    # Pass promoted_at as authority epoch: violations before this timestamp
    # cannot demote the current authority binding (they belong to a prior epoch)
    router_state = RouterState(authority_epoch_ts=authority_state.promoted_at)
    reader = SpineReader(input_spine)
    # In replay mode, demotion events go to output_spine
    gated_router = AuthorityGatedRouter(authority_state, router_state, output_spine)

    print(f"router v{ROUTER_VERSION} replay mode", file=sys.stderr, flush=True)
    print(f"authority_level: {authority_state.level}", file=sys.stderr, flush=True)
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
        router_state.update_from_event(event)

        # Check demotion triggers
        gated_router.check_demotion()

        # Synthesize intent for symbols affected by this event
        symbols_to_check = set()

        if event_type in ("market.regime", "market.regime_change"):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in ("listener.start", "listener.crash", "invariant.violation"):
            symbols_to_check.update(router_state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        for symbol in symbols_to_check:
            if not emit_allowed:
                continue

            # Evaluate constraints → intent or veto reason, with strength classification
            state_dict = {
                "system": router_state.system,
                "symbols": router_state.symbols,
                "degraded_symbols": router_state.get_degraded_symbols(),
            }
            result, intent_strength = evaluate_constraints_with_strength(state_dict, symbol)

            if isinstance(result, VetoReason):
                # Veto path: typed silence
                last_veto = router_state.get_last_veto_reason(symbol)
                if result.value != last_veto:
                    emit_veto(
                        spine_path=output_spine,
                        symbol=symbol,
                        veto_reason=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    router_state.set_last_veto_reason(symbol, result.value)
            elif isinstance(result, AllocationResult):
                # Intent path: positive exposure authority (v0.2 allocator)

                # WEAK INTENT PATH: questions, not decisions
                # - NOT gated by authority
                # - Do NOT update state.last_allocation (independent tracking)
                # - Always emit (no dedup against strong intents)
                if intent_strength == IntentStrength.WEAK:
                    emit_weak_intent(
                        spine_path=output_spine,
                        symbol=symbol,
                        allocation=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    # NOTE: Do NOT update router_state.set_last_allocation()
                    # Weak intents track independently from strong intents
                    continue

                # STRONG INTENT PATH: decisions (GATED)
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit shadow intent (counterfactual) + veto
                    last_allocation = router_state.get_last_allocation(symbol)
                    if should_emit_intent(result, last_allocation):
                        emit_shadow_intent(
                            spine_path=output_spine,
                            symbol=symbol,
                            allocation=result,
                            blocked_by="authority_gate",
                            source_event_id=event_id,
                            source_ts=timestamp,
                        )
                        router_state.set_last_allocation(symbol, result)

                    last_veto = router_state.get_last_veto_reason(symbol)
                    if VetoReason.AUTHORITY_GATE.value != last_veto:
                        emit_veto(
                            spine_path=output_spine,
                            symbol=symbol,
                            veto_reason=VetoReason.AUTHORITY_GATE,
                            source_event_id=event_id,
                            source_ts=timestamp,
                        )
                        router_state.set_last_veto_reason(symbol, VetoReason.AUTHORITY_GATE.value)
                    continue  # Skip real intent emission

                last_allocation = router_state.get_last_allocation(symbol)
                if should_emit_intent(result, last_allocation):
                    emit_intent(
                        spine_path=output_spine,
                        symbol=symbol,
                        allocation=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
                    router_state.set_last_allocation(symbol, result)

    print("replay complete", file=sys.stderr, flush=True)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description=f"Router v{ROUTER_VERSION} - Deterministic intent synthesizer with authority tiers"
    )
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
    parser.add_argument(
        "--cert",
        type=Path,
        help="Path to PROMOTION_CERT_v0_2.json for authority binding (omit for v0.1)",
    )
    parser.add_argument(
        "--demotions-dir",
        type=Path,
        help="Directory for authority_demotions.jsonl (default: spine parent dir)",
    )
    parser.add_argument(
        "--allow-legacy-cert",
        action="store_true",
        help="DANGEROUS: Accept unsigned (legacy self-hash) certificates. Development only.",
    )
    parser.add_argument(
        "--mock-kernel",
        action="store_true",
        help="Use mock envelope kernel instead of real bootstrap MC (for testing/fallback)",
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="Heartbeat interval in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--soak-contract",
        type=Path,
        help="Path to SOAK_CONTRACT.json for formal soak tracking",
    )

    args = parser.parse_args()

    # Compute build metadata
    # Assumes router is run from repo root or we can find it
    repo_root = Path(__file__).parent.parent.parent.parent
    if not (repo_root / "packages" / "router").exists():
        # Try current directory
        repo_root = Path.cwd()

    build_meta = compute_build_metadata(repo_root)
    router_commit = get_router_commit(repo_root)

    # Gate 1: Set build identity for all emissions
    router_build_sha = build_meta['combined_sha256'][:16]
    set_router_build_sha(router_build_sha)

    # Load soak contract if provided
    if args.soak_contract:
        contract_path = args.soak_contract.expanduser().resolve()
        if contract_path.exists():
            contract_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:16]
            set_soak_contract_hash(contract_hash)
            print(f"soak_contract: {contract_path.name}", file=sys.stderr)
            print(f"soak_contract_hash: {contract_hash}", file=sys.stderr)
        else:
            print(f"WARNING: soak contract not found: {contract_path}", file=sys.stderr)

    print(f"router_commit: {router_commit[:8] if router_commit != 'unknown' else 'unknown'}", file=sys.stderr)
    print(f"router_build_sha: {router_build_sha}", file=sys.stderr)
    print(f"build_meta.combined: {build_meta['combined_sha256'][:16]}...", file=sys.stderr)

    # Authority binding
    cert_path = args.cert.expanduser().resolve() if args.cert else None
    authority_state = bind_authority(
        cert_path=cert_path,
        current_build_meta=build_meta,
        allow_legacy_cert=args.allow_legacy_cert,
    )

    # Demotions path
    demotions_path = None
    if args.demotions_dir:
        demotions_dir = args.demotions_dir.expanduser().resolve()
        demotions_dir.mkdir(parents=True, exist_ok=True)
        demotions_path = demotions_dir / "authority_demotions.jsonl"
    elif not args.replay:
        # Default to spine parent directory
        demotions_path = args.spine.parent / "authority_demotions.jsonl"

    if args.replay:
        input_spine = Path(args.replay[0])
        output_spine = Path(args.replay[1])
        run_replay(input_spine, output_spine, authority_state)
    else:
        use_real_kernel = not args.mock_kernel
        run_runtime(
            args.spine,
            args.poll,
            authority_state,
            demotions_path,
            use_real_kernel,
            heartbeat_interval=args.heartbeat,
        )


if __name__ == "__main__":
    main()
