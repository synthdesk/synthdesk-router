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
import atexit
from bisect import bisect_right
import hashlib
import json
import os
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
from router.portfolio import (
    build_correlation_matrix_from_state,
    get_correlation_bucket,
    portfolio_allocate,
)
from emission.speech_counters import INVALID_TIMESTAMP, OTHER_INVARIANT_FAIL, SpeechCounters
from router.emit import (
    emit_decision,
    emit_health_summary,
    emit_heartbeat,
    emit_intent,
    emit_portfolio_summary,
    emit_shadow_intent,
    emit_veto,
    emit_weak_intent,
    set_router_build_sha,
    set_soak_contract_hash,
)
from router.envelope import DEFAULT_HORIZON_MINUTES
from router.surface_integration import SurfaceCache, evaluate_surface_gate
from router.surface_policy import PolicyId, decision_to_dict
from router.envelope_provider import TickBuffer
from router.spine_reader import SpineReader
from router.state import RouterState
from synthdesk_spine.event_types import (
    INVARIANT_VIOLATION,
    LISTENER_CRASH,
    LISTENER_START,
    MARKET_REGIME,
    MARKET_REGIME_CHANGE,
    PERCEPTION_PRICE_LIVENESS,
    ROUTER_AUTHORITY_DEMOTION,
    SPECTRAL_EMIT,
)

# Version
ROUTER_VERSION = "0.2"

# CONSTITUTIONAL: Schema hash enforcement (turns drift into boot failure)
# v1.0 frozen: 2026-01-23 (dad3e1d2824262be)
# v1.1 amended: 2026-01-23 (c4bdc5fe80e2d991) - added decision_authority
# v1.2 amended: 2026-01-24 (aa89ee67ccf3083e) - added classification fields
from schemas.router_decision import ROUTER_DECISION_SCHEMA_HASH
assert ROUTER_DECISION_SCHEMA_HASH == "aa89ee67ccf3083e", (
    f"Schema drift detected: expected aa89ee67ccf3083e, got {ROUTER_DECISION_SCHEMA_HASH}"
)

# Default spine path (configurable via CLI)
DEFAULT_SPINE_PATH = Path("/root/synthdesk-listener/runs/0.2.0/event_spine.jsonl")
DEFAULT_POLL_INTERVAL = 1.0

# Event types the router consumes
ALLOWED_EVENT_TYPES = {
    LISTENER_START,
    LISTENER_CRASH,
    INVARIANT_VIOLATION,
    MARKET_REGIME,
    MARKET_REGIME_CHANGE,
    SPECTRAL_EMIT,  # effective_rank feed for risk envelope (read-only, no authority)
}

# Shadow observation horizons (minutes)
# Multi-horizon shadow emission for EVT-0/EVT-1 diagnostic coverage.
# This is purely observational - does NOT change any decision logic.
# Constitutional: adding observability, not tuning behavior.
SHADOW_OBSERVATION_HORIZONS = [5, 10, 12, 15, 20, 30, 45, 60]

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
    "packages/router/router/portfolio.py",
    "packages/router/router/cross_asset.py",
    "packages/router/router/epistemic.py",
    "packages/router/router/horizon.py",
    # Authority management
    "packages/router/router/authority.py",
    "packages/router/router/signing.py",
    # Trust root - CRITICAL: changing the verifier key invalidates certs
    "packages/router/router/public_key.b64",
    # Schemas
    "packages/router/schemas/router_intent.py",
    "packages/router/schemas/router_decision.py",
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


def _load_ticks_by_symbol(tick_path: Path) -> Dict[str, list[tuple[float, float]]]:
    """Load tick observations into per-symbol lists of (ts_epoch, price)."""
    ticks: Dict[str, list[tuple[float, float]]] = {}
    with tick_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
            except json.JSONDecodeError:
                continue
            symbol = tick.get("asset")
            price = tick.get("price")
            ts_epoch = _parse_tick_timestamp(tick.get("ts_utc"))
            if not symbol or ts_epoch is None or price is None:
                continue
            ticks.setdefault(symbol, []).append((ts_epoch, float(price)))

    for symbol in ticks:
        ticks[symbol].sort(key=lambda t: t[0])
    return ticks


def _get_tick_prices(
    ticks_by_symbol: Dict[str, list[tuple[float, float]]],
    symbol: str,
    asof_ts: Optional[float],
    max_n: int = 1200,
) -> Optional[list[float]]:
    """Return last max_n prices at or before asof_ts for symbol."""
    if asof_ts is None:
        return None
    ticks = ticks_by_symbol.get(symbol, [])
    if not ticks:
        return None
    idx = bisect_right(ticks, (asof_ts, float("inf")))
    start = max(0, idx - max_n)
    return [price for _, price in ticks[start:idx]]


def _env_flag(name: str, default: str = "1") -> bool:
    """
    Deterministic environment flag parser.

    Truthy: 1/true/yes/on
    Falsy: 0/false/no/off/empty
    """
    raw = os.environ.get(name, default)
    if raw is None:
        return False
    value = str(raw).strip().lower()
    if value in ("0", "false", "no", "off", ""):
        return False
    return True


def _choose_portfolio_anchor(symbol_allocations: Dict[str, AllocationResult]) -> str:
    """
    Choose anchor symbol for correlation penalty.

    Preference order:
    1) ROUTER_PORTFOLIO_ANCHOR_SYMBOL (if present in current allocation set)
    2) Common BTC symbols
    3) Largest allocation in this cycle
    """
    env_anchor = os.environ.get("ROUTER_PORTFOLIO_ANCHOR_SYMBOL")
    if env_anchor and env_anchor in symbol_allocations:
        return env_anchor

    for candidate in ("BTCUSDT", "BTC-USD", "BTCUSD", "XBTUSD"):
        if candidate in symbol_allocations:
            return candidate

    return max(symbol_allocations.items(), key=lambda kv: kv[1].size_pct_q)[0]


def _apply_portfolio_allocation(
    state_dict: Dict[str, Any],
    per_symbol: Dict[str, tuple[Any, Optional[IntentStrength], Any]],
) -> Optional[Dict[str, Any]]:
    """
    Mutate per_symbol in-place: apply correlation penalty + 100% total exposure cap.

    Contract:
    - no direction changes
    - only size_pct_q changes
    - rationale appends audit tags
    - fail-closed: missing context degrades to no-op

    Returns:
    - None if portfolio pass did not run
    - Summary payload for router.portfolio.v0 when pass is applied
    """
    # Feature-gated: default OFF; must be explicitly enabled per host.
    if not _env_flag("ROUTER_PORTFOLIO_ENABLE", "0"):
        return None

    raw_allocations: Dict[str, AllocationResult] = {}
    for symbol, (result, _strength, _speech) in per_symbol.items():
        if (
            isinstance(result, AllocationResult)
            and result.direction != Direction.FLAT
            and result.size_pct_q > 0
        ):
            raw_allocations[symbol] = result

    if len(raw_allocations) < 2:
        return None

    anchor_symbol = _choose_portfolio_anchor(raw_allocations)
    symbols = [anchor_symbol] + sorted(s for s in raw_allocations if s != anchor_symbol)
    corr_matrix = build_correlation_matrix_from_state(state_dict, symbols)
    portfolio = portfolio_allocate(raw_allocations, corr_matrix, anchor_symbol=anchor_symbol)
    total_before_q = sum(alloc.size_pct_q for alloc in raw_allocations.values())
    summary_symbols: list[Dict[str, Any]] = []

    for symbol in sorted(portfolio.allocations.keys()):
        sym_alloc = portfolio.allocations[symbol]
        if symbol not in raw_allocations:
            continue

        original = raw_allocations[symbol]
        new_size_q = sym_alloc.adjusted_size_q
        clamp_tag = None

        # Avoid non-flat allocations being rounded to zero.
        if original.size_pct_q > 0 and new_size_q == 0:
            new_size_q = 1
            clamp_tag = "portfolio:zero_clamped_to_1"

        rationale = list(original.rationale)
        rationale.append(f"portfolio_anchor={anchor_symbol}")
        for item in sym_alloc.rationale:
            rationale.append(f"portfolio:{item}")
        if portfolio.exposure_cap_applied:
            rationale.append("portfolio:exposure_cap_applied")
        if clamp_tag is not None:
            rationale.append(clamp_tag)

        updated = AllocationResult(
            direction=original.direction,
            size_pct_q=new_size_q,
            size_pct_scale=original.size_pct_scale,
            risk_cap=original.risk_cap,
            rationale=rationale,
            base_allocation_q=original.base_allocation_q,
            entropy_factor=original.entropy_factor,
            uncertainty_discount=original.uncertainty_discount,
            final_factor=original.final_factor,
            epistemic_discount=original.epistemic_discount,
            epistemic_rationale=original.epistemic_rationale,
            epistemic_metadata=original.epistemic_metadata,
        )

        _, strength, speech_cycle = per_symbol[symbol]
        per_symbol[symbol] = (updated, strength, speech_cycle)

        if symbol == anchor_symbol:
            corr_bucket = "anchor"
            corr_value: Optional[float] = None
        else:
            corr_value = float(corr_matrix.get((symbol, anchor_symbol), 0.0))
            corr_bucket = get_correlation_bucket(corr_value).value

        summary_symbols.append(
            {
                "symbol": symbol,
                "direction": original.direction.value,
                "orig_size_q": original.size_pct_q,
                "adjusted_size_q": new_size_q,
                "corr_bucket": corr_bucket,
                "corr_value": corr_value,
                "is_anchor": symbol == anchor_symbol,
                "penalty_applied": sym_alloc.correlation_penalty > 0.0,
            }
        )

    total_after_q = sum(item["adjusted_size_q"] for item in summary_symbols)
    return {
        "anchor_symbol": anchor_symbol,
        "total_before_q": total_before_q,
        "total_after_q": total_after_q,
        "exposure_cap_applied": portfolio.exposure_cap_applied,
        "penalties_applied": portfolio.correlation_penalties_applied,
        "symbols": summary_symbols,
    }


class TickWatcher:
    """
    Watch tick observation files and maintain rolling buffer per symbol.

    CRITICAL: Stores tick timestamps to enforce tick_ts <= event_ts constraint.
    This prevents future data leakage when router replays historical events.

    Tick files are daily: runs/0.2.0/YYYY-MM-DD/tick_observation.jsonl

    Also emits perception.price_liveness events to the spine for each
    ingested tick. This is infrastructure telemetry (fact pulse), not
    epistemic judgment. See: PERCEPTION_PRICE_LIVENESS event type.
    """

    def __init__(self, runs_dir: Path, tick_buffer: TickBuffer, spine_path: Optional[Path] = None):
        """
        Initialize tick watcher.

        Args:
            runs_dir: Path to runs/0.2.0 directory
            tick_buffer: Buffer to populate with ticks (must store timestamps)
            spine_path: Path to event_spine.jsonl for price liveness emission.
                       None = no liveness emission (e.g. unit tests).
        """
        self.runs_dir = runs_dir
        self.tick_buffer = tick_buffer
        self.spine_path = spine_path
        self._last_positions: Dict[Path, int] = {}
        self._liveness_seq: int = 0

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
                            self._emit_price_liveness(symbol, ts_str)
                    except json.JSONDecodeError:
                        continue
                self._last_positions[tick_file] = f.tell()
        except OSError:
            pass

        return count

    def _emit_price_liveness(self, asset: str, price_ts_str: str) -> None:
        """
        Emit a perception.price_liveness event to the spine.

        Infrastructure telemetry: "a price was seen at time t".
        No epistemic judgment. No predicate semantics.
        """
        if self.spine_path is None:
            return
        recv_ts = datetime.now(timezone.utc).isoformat()
        self._liveness_seq += 1
        event = {
            "event_type": PERCEPTION_PRICE_LIVENESS,
            "timestamp": recv_ts,
            "payload": {
                "asset": asset,
                "price_ts": price_ts_str,
                "recv_ts": recv_ts,
                "venue": "tick_observation_jsonl",
                "seq": self._liveness_seq,
            },
        }
        try:
            with self.spine_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
                f.flush()
        except OSError:
            pass


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
        "event_type": ROUTER_AUTHORITY_DEMOTION,
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
    regime_stream_path: Optional[Path] = None,
    micro_stream_path: Optional[Path] = None,
    surface_policy_id: PolicyId = PolicyId.P_HOLD_BLOCK_REGIME,
    intended_hold_min: int = DEFAULT_HORIZON_MINUTES,
) -> None:
    """
    Long-running router runtime with authority gating.

    DOCTRINE: VETO_TIMESCALE - Integrates surface gate evaluation.

    Args:
        spine_path: Path to event_spine.jsonl
        poll_interval: Seconds between polls
        authority_state: Bound authority state
        demotions_path: Path for durable demotion recording
        use_real_kernel: If True, use bootstrap MC kernel (requires tick data)
        heartbeat_interval: Seconds between heartbeat emissions
        regime_stream_path: Path to regime veto surface JSONL stream (optional)
        micro_stream_path: Path to micro veto surface JSONL stream (optional)
        surface_policy_id: Surface policy to apply (default: P_HOLD_BLOCK_REGIME)
        intended_hold_min: Intended holding period in minutes (REQUIRED for surface gate)
    """
    # Pass promoted_at as authority epoch: violations before this timestamp
    # cannot demote the current authority binding (they belong to a prior epoch)
    router_state = RouterState(authority_epoch_ts=authority_state.promoted_at)
    reader = SpineReader(spine_path, poll_interval)
    gated_router = AuthorityGatedRouter(authority_state, router_state, spine_path, demotions_path)

    # Setup tick buffer and watcher for real MC kernel
    tick_buffer = TickBuffer(max_size=1200)  # ~20 minutes at 1 tick/sec
    runs_dir = spine_path.parent  # Assume spine is in runs/0.2.0/
    tick_watcher = TickWatcher(runs_dir, tick_buffer, spine_path=spine_path)
    router_runs_root = Path(__file__).resolve().parents[1] / "runs"
    speech_counters = SpeechCounters(runs_root=router_runs_root)
    atexit.register(speech_counters.flush)

    # DOCTRINE: VETO_TIMESCALE - Surface cache for time-scale aware blocking
    surface_cache = SurfaceCache(
        regime_stream_path=regime_stream_path,
        micro_stream_path=micro_stream_path,
    )
    surface_gate_enabled = regime_stream_path is not None or micro_stream_path is not None

    # Heartbeat state
    last_heartbeat_time = time.time()
    events_consumed = 0
    intents_emitted = 0
    vetoes_emitted = 0
    shadows_emitted = 0
    last_event_ts: Optional[str] = None
    # Health summary state (30-minute window)
    HEALTH_SUMMARY_INTERVAL = 30 * 60
    start_time = time.time()
    last_health_summary_time = start_time
    window_events = 0
    window_intents = 0
    window_vetoes = 0
    window_shadows = 0
    window_violations = 0
    window_per_symbol: Dict[str, int] = {}

    print(f"router v{ROUTER_VERSION} runtime started", file=sys.stderr, flush=True)
    print(f"authority_level: {authority_state.level}", file=sys.stderr, flush=True)
    print(f"spine: {spine_path}", file=sys.stderr, flush=True)
    print(f"poll: {poll_interval}s", file=sys.stderr, flush=True)
    print(f"real_kernel: {use_real_kernel}", file=sys.stderr, flush=True)
    print(f"heartbeat_interval: {heartbeat_interval}s", file=sys.stderr, flush=True)
    # DOCTRINE: VETO_TIMESCALE - Surface gate configuration
    print(f"surface_gate_enabled: {surface_gate_enabled}", file=sys.stderr, flush=True)
    if surface_gate_enabled:
        print(f"surface_policy: {surface_policy_id.value}", file=sys.stderr, flush=True)
        print(f"intended_hold_min: {intended_hold_min}", file=sys.stderr, flush=True)
        print(f"regime_stream: {regime_stream_path}", file=sys.stderr, flush=True)
        print(f"micro_stream: {micro_stream_path}", file=sys.stderr, flush=True)

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

        if now - last_health_summary_time >= HEALTH_SUMMARY_INTERVAL:
            lag_s = 0.0
            if last_event_ts:
                try:
                    last_dt = datetime.fromisoformat(last_event_ts.replace("Z", "+00:00"))
                    lag_s = (datetime.now(timezone.utc) - last_dt).total_seconds()
                except (ValueError, TypeError):
                    lag_s = 0.0
            per_symbol_regimes = {
                sym: data.get("regime", "unknown") for sym, data in router_state.symbols.items()
            }
            emit_health_summary(
                spine_path=spine_path,
                authority_level=str(authority_state.level),
                window_s=int(now - last_health_summary_time),
                events_in_window=window_events,
                intents_in_window=window_intents,
                vetoes_in_window=window_vetoes,
                shadows_in_window=window_shadows,
                violations_in_window=window_violations,
                per_symbol_events=dict(window_per_symbol),
                per_symbol_regimes=per_symbol_regimes,
                lag_s=lag_s,
                last_event_ts=last_event_ts,
                uptime_s=now - start_time,
            )
            last_health_summary_time = now
            window_events = 0
            window_intents = 0
            window_vetoes = 0
            window_shadows = 0
            window_violations = 0
            window_per_symbol = {}

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
        # Track window counters for health summary
        window_events += 1
        if event_type in (MARKET_REGIME, MARKET_REGIME_CHANGE):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                window_per_symbol[symbol] = window_per_symbol.get(symbol, 0) + 1
        if event_type == INVARIANT_VIOLATION:
            window_violations += 1

        # Update state from event
        router_state.update_from_event(event)

        # Check demotion triggers after state update
        gated_router.check_demotion()

        # Synthesize intent for symbols affected by this event
        symbols_to_check = set()

        if event_type in (MARKET_REGIME, MARKET_REGIME_CHANGE):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in (LISTENER_START, LISTENER_CRASH, INVARIANT_VIOLATION):
            symbols_to_check.update(router_state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        # Sort symbols for deterministic emission order.
        symbols_order = sorted(symbols_to_check)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        state_dict = {
            "system": router_state.system,
            "symbols": router_state.symbols,
            "degraded_symbols": router_state.get_degraded_symbols(),
        }
        per_symbol: Dict[str, tuple[Any, Optional[IntentStrength], Any]] = {}

        # Phase 1: evaluate constraints.
        for symbol in symbols_order:
            speech_cycle = speech_counters.tick()
            if not emit_allowed:
                if not isinstance(timestamp, str):
                    speech_cycle.observe(None, INVALID_TIMESTAMP)
                else:
                    speech_cycle.observe(None, OTHER_INVARIANT_FAIL)
                speech_cycle.finalize()
                continue
            result, intent_strength = evaluate_constraints_with_strength(state_dict, symbol)
            per_symbol[symbol] = (result, intent_strength, speech_cycle)

        # Phase 2: portfolio-level adjustment (correlation penalty + 100% cap).
        portfolio_summary = _apply_portfolio_allocation(state_dict, per_symbol)
        if portfolio_summary is not None:
            emit_portfolio_summary(
                spine_path=spine_path,
                summary=portfolio_summary,
                source_event_id=event_id,
                source_ts=timestamp,
            )

        # Phase 3: emit according to authority + surface gates.
        for symbol in symbols_order:
            entry = per_symbol.get(symbol)
            if entry is None:
                continue
            result, intent_strength, speech_cycle = entry

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
                        speech_cycle=speech_cycle,
                    )
                    vetoes_emitted += 1
                    window_vetoes += 1
                    router_state.set_last_veto_reason(symbol, result.value)
            elif isinstance(result, AllocationResult):
                # Intent path: positive exposure authority (v0.2 allocator)

                # WEAK INTENT PATH: questions, not decisions
                # - NOT gated by authority
                # - Do NOT update state.last_allocation (independent tracking)
                # - Always emit (no dedup against strong intents)
                # - AMENDED: Emit decision event for observability (decision_authority="weak")
                if intent_strength == IntentStrength.WEAK:
                    # DOCTRINE: VETO_TIMESCALE - Surface gate evaluation for weak intents
                    # Emits decision event for observability; weak intents always emit regardless of gate
                    if surface_gate_enabled:
                        regime_surface, micro_surface = surface_cache.get_surfaces(symbol)
                        weak_allowed, weak_veto_reason, weak_decision = evaluate_surface_gate(
                            asset=symbol,
                            intended_hold_min=intended_hold_min,
                            regime_surface=regime_surface,
                            micro_surface=micro_surface,
                            policy_id=surface_policy_id,
                        )
                        weak_decision_dict = decision_to_dict(weak_decision)
                        emit_decision(
                            spine_path=spine_path,
                            symbol=symbol,
                            intended_hold_min=intended_hold_min,
                            policy_id=weak_decision.policy_id,
                            policy_defaulted=weak_decision.policy_defaulted,
                            allowed=weak_decision.allowed,
                            veto_reason=weak_veto_reason,
                            blocks=weak_decision_dict["blocks"],
                            annotations=weak_decision_dict["annotations"],
                            attached_surfaces=weak_decision_dict["attached_surfaces"],
                            source_event_id=event_id,
                            source_ts=timestamp,
                            decision_authority="weak",
                        )
                        # NOTE: Weak intents always emit regardless of gate decision
                        # The decision event is purely for observability

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
                        speech_cycle=speech_cycle,
                    )
                    intents_emitted += 1  # Count weak intents too
                    window_intents += 1
                    # NOTE: Do NOT update router_state.set_last_allocation()
                    # Weak intents track independently from strong intents
                    speech_cycle.finalize()
                    continue

                # STRONG INTENT PATH: decisions
                # GATED: non-flat requires v0.2+ authority
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit shadow intent (counterfactual) + veto
                    # Shadow intent feeds EVT-1 without granting real authority
                    # Multi-horizon: emit one shadow per observation horizon (observability only)
                    # AMENDED: Emit decision event for observability (decision_authority="shadow")
                    last_allocation = router_state.get_last_allocation(symbol)
                    if should_emit_intent(result, last_allocation):
                        # DOCTRINE: VETO_TIMESCALE - Surface gate evaluation for shadow intents
                        # Emits decision event for observability; shadow intents always emit regardless of gate
                        if surface_gate_enabled:
                            regime_surface, micro_surface = surface_cache.get_surfaces(symbol)
                            shadow_allowed, shadow_veto_reason, shadow_decision = evaluate_surface_gate(
                                asset=symbol,
                                intended_hold_min=intended_hold_min,
                                regime_surface=regime_surface,
                                micro_surface=micro_surface,
                                policy_id=surface_policy_id,
                            )
                            shadow_decision_dict = decision_to_dict(shadow_decision)
                            emit_decision(
                                spine_path=spine_path,
                                symbol=symbol,
                                intended_hold_min=intended_hold_min,
                                policy_id=shadow_decision.policy_id,
                                policy_defaulted=shadow_decision.policy_defaulted,
                                allowed=shadow_decision.allowed,
                                veto_reason=shadow_veto_reason,
                                blocks=shadow_decision_dict["blocks"],
                                annotations=shadow_decision_dict["annotations"],
                                attached_surfaces=shadow_decision_dict["attached_surfaces"],
                                source_event_id=event_id,
                                source_ts=timestamp,
                                decision_authority="shadow",
                            )
                            # NOTE: Shadow intents always emit regardless of gate decision
                            # The decision event is purely for observability

                        for horizon_min in SHADOW_OBSERVATION_HORIZONS:
                            emit_shadow_intent(
                                spine_path=spine_path,
                                symbol=symbol,
                                allocation=result,
                                blocked_by="authority_gate",
                                source_event_id=event_id,
                                source_ts=timestamp,
                                tick_prices=tick_prices,
                                use_real_kernel=use_real_kernel,
                                horizon_minutes=horizon_min,
                                z_mean=z_mean,
                                z_std=z_std,
                                regime=regime,
                                regime_confidence=regime_confidence,
                                speech_cycle=speech_cycle,
                            )
                            shadows_emitted += 1
                            window_shadows += 1
                        router_state.set_last_allocation(symbol, result)

                    last_veto = router_state.get_last_veto_reason(symbol)
                    if VetoReason.AUTHORITY_GATE.value != last_veto:
                        emit_veto(
                            spine_path=spine_path,
                            symbol=symbol,
                            veto_reason=VetoReason.AUTHORITY_GATE,
                            source_event_id=event_id,
                            source_ts=timestamp,
                            speech_cycle=speech_cycle,
                        )
                        vetoes_emitted += 1
                        window_vetoes += 1
                        router_state.set_last_veto_reason(symbol, VetoReason.AUTHORITY_GATE.value)
                    speech_cycle.finalize()
                    continue  # Skip real intent emission

                last_allocation = router_state.get_last_allocation(symbol)
                if should_emit_intent(result, last_allocation):
                    # DOCTRINE: VETO_TIMESCALE - Surface gate evaluation
                    # Evaluate BEFORE emitting intent; if blocked, emit veto instead
                    if surface_gate_enabled:
                        regime_surface, micro_surface = surface_cache.get_surfaces(symbol)
                        gate_allowed, gate_veto_reason, gate_decision = evaluate_surface_gate(
                            asset=symbol,
                            intended_hold_min=intended_hold_min,
                            regime_surface=regime_surface,
                            micro_surface=micro_surface,
                            policy_id=surface_policy_id,
                        )

                        # Always emit decision event (audit trail)
                        decision_dict = decision_to_dict(gate_decision)
                        emit_decision(
                            spine_path=spine_path,
                            symbol=symbol,
                            intended_hold_min=intended_hold_min,
                            policy_id=gate_decision.policy_id,
                            policy_defaulted=gate_decision.policy_defaulted,
                            allowed=gate_decision.allowed,
                            veto_reason=gate_veto_reason,
                            blocks=decision_dict["blocks"],
                            annotations=decision_dict["annotations"],
                            attached_surfaces=decision_dict["attached_surfaces"],
                            source_event_id=event_id,
                            source_ts=timestamp,
                            decision_authority="real",
                        )

                        if not gate_allowed:
                            # Surface gate blocked: emit veto (abstention, not rejection)
                            last_veto = router_state.get_last_veto_reason(symbol)
                            if gate_veto_reason.value != last_veto:
                                emit_veto(
                                    spine_path=spine_path,
                                    symbol=symbol,
                                    veto_reason=gate_veto_reason,
                                    source_event_id=event_id,
                                    source_ts=timestamp,
                                    speech_cycle=speech_cycle,
                                )
                                vetoes_emitted += 1
                                window_vetoes += 1
                                router_state.set_last_veto_reason(symbol, gate_veto_reason.value)
                            speech_cycle.finalize()
                            continue  # Skip intent emission

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
                        speech_cycle=speech_cycle,
                    )
                    intents_emitted += 1
                    window_intents += 1
                    router_state.set_last_allocation(symbol, result)
            speech_cycle.finalize()


def run_replay(
    input_spine: Path,
    output_spine: Path,
    authority_state: AuthorityState,
    ticks_by_symbol: Optional[Dict[str, list[tuple[float, float]]]] = None,
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

        if event_type in (MARKET_REGIME, MARKET_REGIME_CHANGE):
            symbol = payload.get("symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, str):
                symbols_to_check.add(symbol)

        # System-wide events affect all symbols
        if event_type in (LISTENER_START, LISTENER_CRASH, INVARIANT_VIOLATION):
            symbols_to_check.update(router_state.symbols.keys())

        # Synthesize and emit (XOR: intent or veto, never both)
        symbols_order = sorted(symbols_to_check)
        emit_allowed = isinstance(event_id, str) and isinstance(timestamp, str)
        if not emit_allowed:
            continue

        state_dict = {
            "system": router_state.system,
            "symbols": router_state.symbols,
            "degraded_symbols": router_state.get_degraded_symbols(),
        }
        per_symbol: Dict[str, tuple[Any, Optional[IntentStrength], Any]] = {}
        for symbol in symbols_order:
            result, intent_strength = evaluate_constraints_with_strength(state_dict, symbol)
            per_symbol[symbol] = (result, intent_strength, None)

        portfolio_summary = _apply_portfolio_allocation(state_dict, per_symbol)
        if portfolio_summary is not None:
            emit_portfolio_summary(
                spine_path=output_spine,
                summary=portfolio_summary,
                source_event_id=event_id,
                source_ts=timestamp,
            )

        for symbol in symbols_order:
            entry = per_symbol.get(symbol)
            if entry is None:
                continue
            result, intent_strength, _ = entry

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
                asof_ts = _parse_tick_timestamp(timestamp)
                tick_prices = (
                    _get_tick_prices(ticks_by_symbol, symbol, asof_ts)
                    if ticks_by_symbol
                    else None
                )
                z_mean = router_state.get_z_mean(symbol)
                z_std = router_state.get_z_std(symbol)
                regime = router_state.get_regime(symbol)
                regime_confidence = router_state.get_regime_confidence(symbol)

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
                        tick_prices=tick_prices,
                        z_mean=z_mean,
                        z_std=z_std,
                        regime=regime,
                        regime_confidence=regime_confidence,
                    )
                    # NOTE: Do NOT update router_state.set_last_allocation()
                    # Weak intents track independently from strong intents
                    continue

                # STRONG INTENT PATH: decisions (GATED)
                if result.direction != Direction.FLAT and not gated_router.can_emit_non_flat():
                    # Authority gate: emit shadow intent (counterfactual) + veto
                    # Multi-horizon: emit one shadow per observation horizon (observability only)
                    last_allocation = router_state.get_last_allocation(symbol)
                    if should_emit_intent(result, last_allocation):
                        for horizon_min in SHADOW_OBSERVATION_HORIZONS:
                            emit_shadow_intent(
                                spine_path=output_spine,
                                symbol=symbol,
                                allocation=result,
                                blocked_by="authority_gate",
                                source_event_id=event_id,
                                source_ts=timestamp,
                                horizon_minutes=horizon_min,
                                tick_prices=tick_prices,
                                z_mean=z_mean,
                                z_std=z_std,
                                regime=regime,
                                regime_confidence=regime_confidence,
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
                        tick_prices=tick_prices,
                        z_mean=z_mean,
                        z_std=z_std,
                        regime=regime,
                        regime_confidence=regime_confidence,
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
        "--ticks",
        type=Path,
        help="Tick observation JSONL (replay mode only)",
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
    # DOCTRINE: VETO_TIMESCALE - Surface gate arguments
    parser.add_argument(
        "--regime-surface-stream",
        type=Path,
        help="Path to regime veto surface JSONL stream (live surfaces)",
    )
    parser.add_argument(
        "--micro-surface-stream",
        type=Path,
        help="Path to micro veto surface JSONL stream (live surfaces)",
    )
    parser.add_argument(
        "--surface-policy",
        type=str,
        choices=["P_HOLD_BLOCK_REGIME", "P_SCALP_BLOCK_MICRO", "P_UNION_SAFETY", "P_ANNOTATE_ONLY"],
        default="P_HOLD_BLOCK_REGIME",
        help="Surface policy to apply (default: P_HOLD_BLOCK_REGIME)",
    )
    parser.add_argument(
        "--intended-hold-min",
        type=int,
        default=DEFAULT_HORIZON_MINUTES,
        help=f"Intended holding period in minutes for surface evaluation (default: {DEFAULT_HORIZON_MINUTES})",
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
        ticks_by_symbol = None
        if args.ticks:
            tick_path = args.ticks.expanduser().resolve()
            if not tick_path.exists():
                print(f"ERROR: ticks file not found: {tick_path}", file=sys.stderr)
                sys.exit(2)
            ticks_by_symbol = _load_ticks_by_symbol(tick_path)
            print(f"loaded_ticks: {tick_path}", file=sys.stderr)
        run_replay(input_spine, output_spine, authority_state, ticks_by_symbol=ticks_by_symbol)
    else:
        use_real_kernel = not args.mock_kernel
        # DOCTRINE: VETO_TIMESCALE - Surface cache configuration
        regime_stream = args.regime_surface_stream.expanduser().resolve() if args.regime_surface_stream else None
        micro_stream = args.micro_surface_stream.expanduser().resolve() if args.micro_surface_stream else None
        surface_policy_id = PolicyId(args.surface_policy)
        run_runtime(
            args.spine,
            args.poll,
            authority_state,
            demotions_path,
            use_real_kernel,
            heartbeat_interval=args.heartbeat,
            regime_stream_path=regime_stream,
            micro_stream_path=micro_stream,
            surface_policy_id=surface_policy_id,
            intended_hold_min=args.intended_hold_min,
        )


if __name__ == "__main__":
    main()
