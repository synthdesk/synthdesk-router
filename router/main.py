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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from router.allocator import AllocationResult, Direction
from router.authority import (
    AuthorityLevel,
    AuthorityState,
    DemotionWatcher,
    bind_authority,
    create_violation_active_check,
)
from router.constraints import VetoReason, evaluate_constraints, should_emit_intent
from router.emit import emit_intent, emit_veto
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

        # Setup demotion watcher
        self.demotion_watcher = DemotionWatcher(authority_state)
        self.demotion_watcher.add_check(
            create_violation_active_check(router_state.is_violation_active)
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
) -> None:
    """
    Long-running router runtime with authority gating.

    Args:
        spine_path: Path to event_spine.jsonl
        poll_interval: Seconds between polls
        authority_state: Bound authority state
        demotions_path: Path for durable demotion recording
    """
    router_state = RouterState()
    reader = SpineReader(spine_path, poll_interval)
    gated_router = AuthorityGatedRouter(authority_state, router_state, spine_path, demotions_path)

    print(f"router v{ROUTER_VERSION} runtime started", file=sys.stderr, flush=True)
    print(f"authority_level: {authority_state.level}", file=sys.stderr, flush=True)
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

            # Evaluate constraints → intent or veto reason
            state_dict = {
                "system": router_state.system,
                "symbols": router_state.symbols,
            }
            result = evaluate_constraints(state_dict, symbol)

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
                    router_state.set_last_veto_reason(symbol, result.value)
            elif isinstance(result, AllocationResult):
                # Intent path: positive exposure authority (v0.2 allocator)
                # GATED: non-flat requires v0.2+ authority
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit veto, not fake flat intent
                    print(
                        f"AUTHORITY GATE: non-flat intent blocked (level={gated_router.get_authority_level()})",
                        file=sys.stderr,
                    )
                    last_veto = router_state.get_last_veto_reason(symbol)
                    if VetoReason.AUTHORITY_GATE.value != last_veto:
                        emit_veto(
                            spine_path=spine_path,
                            symbol=symbol,
                            veto_reason=VetoReason.AUTHORITY_GATE,
                            source_event_id=event_id,
                            source_ts=timestamp,
                        )
                        router_state.set_last_veto_reason(symbol, VetoReason.AUTHORITY_GATE.value)
                    continue  # Skip intent emission entirely

                last_allocation = router_state.get_last_allocation(symbol)
                if should_emit_intent(result, last_allocation):
                    emit_intent(
                        spine_path=spine_path,
                        symbol=symbol,
                        allocation=result,
                        source_event_id=event_id,
                        source_ts=timestamp,
                    )
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
    router_state = RouterState()
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

            # Evaluate constraints → intent or veto reason
            state_dict = {
                "system": router_state.system,
                "symbols": router_state.symbols,
            }
            result = evaluate_constraints(state_dict, symbol)

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
                # Intent path: positive exposure authority (GATED, v0.2 allocator)
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit veto, not fake flat intent
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
                    continue  # Skip intent emission entirely

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

    args = parser.parse_args()

    # Compute build metadata
    # Assumes router is run from repo root or we can find it
    repo_root = Path(__file__).parent.parent.parent.parent
    if not (repo_root / "packages" / "router").exists():
        # Try current directory
        repo_root = Path.cwd()

    build_meta = compute_build_metadata(repo_root)
    router_commit = get_router_commit(repo_root)

    print(f"router_commit: {router_commit[:8] if router_commit != 'unknown' else 'unknown'}", file=sys.stderr)
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
        run_runtime(args.spine, args.poll, authority_state, demotions_path)


if __name__ == "__main__":
    main()
