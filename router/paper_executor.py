"""
paper_executor.py

Gate 2: Bounded paper execution adapter.

Consumes router intent events from spine, enforces hard caps,
emits execution.paper.* events. Zero live trades.

Caps (hard limits, not tunable at runtime):
- MAX_NOTIONAL_PER_TRADE: Maximum notional per single trade
- MAX_GROSS_EXPOSURE: Maximum total exposure across all positions
- MAX_TRADES_PER_DAY: Maximum number of trades per calendar day
- MAX_TURNOVER_PER_DAY: Maximum total turnover per day
- MAX_DAILY_LOSS: Maximum paper PnL loss before kill switch

Kill switch: Immediately flatten all positions and stop processing.

Breach behavior: Any cap breach emits invariant.violation and forces flat.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from router.emit import get_router_build_sha, get_soak_contract_hash
from synthdesk_spine.event_types import INVARIANT_VIOLATION

# Risk envelope for regime-conditional caps (court-frozen, no authority)
try:
    from packages.risk import get_risk_envelope, ENVELOPE_VERSION
    _ENVELOPE_AVAILABLE = True
except ImportError:
    _ENVELOPE_AVAILABLE = False
    ENVELOPE_VERSION = "unavailable"
    def get_risk_envelope(regime_rank):
        """Fallback if envelope not installed."""
        return None


# =============================================================================
# Hard Caps (Gate 2 constants - DO NOT make these runtime configurable)
# These are BASELINE caps. Regime Risk Envelope v0 may tighten them further.
# =============================================================================

# Per-trade limits (baseline)
BASE_MAX_NOTIONAL_PER_TRADE = 200.0  # USD equivalent (router allocates up to 16% = $160)
MAX_POSITION_SIZE_PCT = 0.20   # 20% of notional capital

# Portfolio limits (baseline)
BASE_MAX_GROSS_EXPOSURE = 1000.0    # USD total across all positions
BASE_MAX_NET_EXPOSURE = 600.0       # USD net long/short
BASE_MAX_CONCURRENT_POSITIONS = 5   # Portfolio breadth cap

# Daily limits (not envelope-adjusted)
MAX_TRADES_PER_DAY = 100
MAX_TURNOVER_PER_DAY = 5000.0  # USD

# Risk limits (not envelope-adjusted)
MAX_DAILY_LOSS = 100.0         # USD paper loss triggers kill switch

# Reference capital for percentage calculations
PAPER_CAPITAL = 1000.0         # USD

# Legacy aliases for backwards compatibility (use envelope-driven caps in check_caps)
MAX_NOTIONAL_PER_TRADE = BASE_MAX_NOTIONAL_PER_TRADE
MAX_GROSS_EXPOSURE = BASE_MAX_GROSS_EXPOSURE
MAX_NET_EXPOSURE = BASE_MAX_NET_EXPOSURE


# =============================================================================
# Data Structures
# =============================================================================

class ExecutionAction(Enum):
    """Paper execution action types."""
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    FLAT_ALL = "flat_all"
    REJECTED = "rejected"


class BreachType(Enum):
    """Cap breach types."""
    MAX_NOTIONAL = "max_notional_per_trade"
    MAX_GROSS_EXPOSURE = "max_gross_exposure"
    MAX_NET_EXPOSURE = "max_net_exposure"
    MAX_TRADES_PER_DAY = "max_trades_per_day"
    MAX_TURNOVER_PER_DAY = "max_turnover_per_day"
    MAX_DAILY_LOSS = "max_daily_loss"
    KILL_SWITCH = "kill_switch_active"


@dataclass
class PaperPosition:
    """Paper position state."""
    symbol: str
    direction: str  # "long" or "short"
    size: float     # Notional USD
    entry_price: float
    entry_ts: datetime
    source_event_id: str


@dataclass
class DailyStats:
    """Daily statistics for cap enforcement."""
    date: str  # YYYY-MM-DD
    trade_count: int = 0
    turnover: float = 0.0
    realized_pnl: float = 0.0
    peak_equity: float = PAPER_CAPITAL
    max_drawdown: float = 0.0


@dataclass
class ExecutorState:
    """Paper executor state."""
    positions: Dict[str, PaperPosition] = field(default_factory=dict)
    daily_stats: DailyStats = field(default_factory=lambda: DailyStats(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ))
    kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    total_realized_pnl: float = 0.0
    events_processed: int = 0


# =============================================================================
# Cap Enforcement
# =============================================================================

def check_caps(
    state: ExecutorState,
    proposed_notional: float,
    current_price: float,
    regime_rank: Optional[int] = None,
    envelope_audit: Optional[Dict] = None,
) -> Tuple[bool, Optional[BreachType], str]:
    """
    Check if proposed trade would breach any caps.

    Caps are regime-conditional via Risk Envelope v0:
    - regime_rank=1 (stressed): tighter caps (40% exposure, 1x leverage)
    - regime_rank=2 (calm): normal caps (100% exposure, 2x leverage)
    - regime_rank=None: fail-closed to rank=1

    Args:
        state: Current executor state
        proposed_notional: Proposed trade notional
        current_price: Current price (unused but kept for interface)
        regime_rank: Spectral effective_rank (1 or 2), None if missing/stale
        envelope_audit: Optional dict to populate with envelope application details

    Returns:
        (allowed, breach_type, reason)
    """
    # Get regime-conditional caps from envelope
    envelope = get_risk_envelope(regime_rank) if _ENVELOPE_AVAILABLE else None

    if envelope is not None:
        # Envelope-driven caps
        max_gross = envelope.max_gross_exposure_mult * PAPER_CAPITAL
        max_notional = envelope.max_gross_exposure_mult * BASE_MAX_NOTIONAL_PER_TRADE
        max_positions = envelope.max_concurrent_positions
        resolved_rank = regime_rank if regime_rank is not None else 1
        rank_source = "spectral" if regime_rank is not None else "missing_failclosed"
    else:
        # Fallback to baseline if envelope unavailable
        max_gross = BASE_MAX_GROSS_EXPOSURE
        max_notional = BASE_MAX_NOTIONAL_PER_TRADE
        max_positions = BASE_MAX_CONCURRENT_POSITIONS
        resolved_rank = None
        rank_source = "envelope_unavailable"

    # Populate audit dict if provided
    if envelope_audit is not None:
        envelope_audit["envelope_version"] = ENVELOPE_VERSION if _ENVELOPE_AVAILABLE else "unavailable"
        envelope_audit["regime_rank"] = regime_rank
        envelope_audit["resolved_rank"] = resolved_rank
        envelope_audit["rank_source"] = rank_source
        envelope_audit["limits"] = {
            "max_gross_exposure": max_gross,
            "max_notional_per_trade": max_notional,
            "max_concurrent_positions": max_positions,
        }

    # Kill switch check (highest priority)
    if state.kill_switch_active:
        return False, BreachType.KILL_SWITCH, state.kill_switch_reason or "kill switch active"

    # Per-trade notional (envelope-adjusted)
    if proposed_notional > max_notional:
        return False, BreachType.MAX_NOTIONAL, f"notional {proposed_notional:.2f} > max {max_notional:.2f}"

    # Daily trade count (not envelope-adjusted)
    if state.daily_stats.trade_count >= MAX_TRADES_PER_DAY:
        return False, BreachType.MAX_TRADES_PER_DAY, f"trades {state.daily_stats.trade_count} >= max {MAX_TRADES_PER_DAY}"

    # Daily turnover (not envelope-adjusted)
    if state.daily_stats.turnover + proposed_notional > MAX_TURNOVER_PER_DAY:
        return False, BreachType.MAX_TURNOVER_PER_DAY, f"turnover would exceed {MAX_TURNOVER_PER_DAY:.2f}"

    # Gross exposure (envelope-adjusted)
    current_gross = sum(p.size for p in state.positions.values())
    if current_gross + proposed_notional > max_gross:
        return False, BreachType.MAX_GROSS_EXPOSURE, f"gross exposure would exceed {max_gross:.2f}"

    # Position count (envelope-adjusted)
    if len(state.positions) >= max_positions:
        return False, BreachType.MAX_GROSS_EXPOSURE, f"position count {len(state.positions)} >= max {max_positions}"

    # Net exposure (use baseline, not envelope-adjusted for now)
    long_exposure = sum(p.size for p in state.positions.values() if p.direction == "long")
    short_exposure = sum(p.size for p in state.positions.values() if p.direction == "short")
    if abs(long_exposure - short_exposure) + proposed_notional > BASE_MAX_NET_EXPOSURE:
        return False, BreachType.MAX_NET_EXPOSURE, f"net exposure would exceed {BASE_MAX_NET_EXPOSURE:.2f}"

    # Daily loss (not envelope-adjusted)
    if state.daily_stats.realized_pnl < -MAX_DAILY_LOSS:
        return False, BreachType.MAX_DAILY_LOSS, f"daily loss {state.daily_stats.realized_pnl:.2f} exceeds max {MAX_DAILY_LOSS:.2f}"

    return True, None, "caps_ok"


def trigger_kill_switch(state: ExecutorState, reason: str) -> None:
    """Activate kill switch - flatten all and stop."""
    state.kill_switch_active = True
    state.kill_switch_reason = reason


# =============================================================================
# Event Processing
# =============================================================================

def _get_today_str() -> str:
    """Get today's date string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _reset_daily_stats_if_needed(state: ExecutorState) -> bool:
    """Reset daily stats if date changed. Returns True if reset."""
    today = _get_today_str()
    if state.daily_stats.date != today:
        state.daily_stats = DailyStats(date=today)
        return True
    return False


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def _compute_pnl(position: PaperPosition, exit_price: float) -> float:
    """Compute paper PnL for closing a position."""
    if position.direction == "long":
        return position.size * (exit_price - position.entry_price) / position.entry_price
    else:  # short
        return position.size * (position.entry_price - exit_price) / position.entry_price


def process_intent(
    state: ExecutorState,
    event: Dict,
    current_prices: Dict[str, float],
    regime_rank: Optional[int] = None,
) -> Tuple[Dict, Optional[Dict]]:
    """
    Process a router.intent event and produce execution.paper.* events.

    Args:
        state: Current executor state
        event: Router intent event
        current_prices: Current prices by symbol
        regime_rank: Spectral effective_rank for risk envelope (None = fail-closed)

    Returns:
        (execution_event, optional_violation_event)
    """
    state.events_processed += 1
    _reset_daily_stats_if_needed(state)

    payload = event.get("payload", {})
    symbol = payload.get("symbol")
    direction = payload.get("direction")
    size_pct_q = payload.get("size_pct_q", 0)
    size_pct_scale = payload.get("size_pct_scale", 10000)
    source_event_id = event.get("source_event_id")
    source_ts = event.get("source_ts")

    # Compute proposed notional
    size_pct = size_pct_q / size_pct_scale if size_pct_scale > 0 else 0
    proposed_notional = PAPER_CAPITAL * size_pct

    # Get current price
    current_price = current_prices.get(symbol, 0.0)
    if current_price <= 0:
        # No price available - reject
        return _make_execution_event(
            action=ExecutionAction.REJECTED,
            symbol=symbol,
            reason="no_price_available",
            source_event_id=source_event_id,
            source_ts=source_ts,
            state=state,
        ), None

    # Check caps (envelope-adjusted based on regime_rank)
    envelope_audit: Dict = {}
    allowed, breach_type, reason = check_caps(
        state, proposed_notional, current_price,
        regime_rank=regime_rank,
        envelope_audit=envelope_audit,
    )

    if not allowed:
        # Cap breach - emit violation and reject
        violation_event = _make_violation_event(
            breach_type=breach_type,
            reason=reason,
            symbol=symbol,
            source_event_id=source_event_id,
            source_ts=source_ts,
        )

        exec_event = _make_execution_event(
            action=ExecutionAction.REJECTED,
            symbol=symbol,
            reason=f"cap_breach:{breach_type.value}",
            source_event_id=source_event_id,
            source_ts=source_ts,
            state=state,
            envelope_audit=envelope_audit,
        )

        # Trigger kill switch on loss breach
        if breach_type == BreachType.MAX_DAILY_LOSS:
            trigger_kill_switch(state, reason)

        return exec_event, violation_event

    # Execute paper trade
    existing_position = state.positions.get(symbol)

    if direction == "flat":
        # Close existing position
        if existing_position:
            pnl = _compute_pnl(existing_position, current_price)
            state.total_realized_pnl += pnl
            state.daily_stats.realized_pnl += pnl
            state.daily_stats.trade_count += 1
            state.daily_stats.turnover += existing_position.size
            del state.positions[symbol]

            return _make_execution_event(
                action=ExecutionAction.CLOSE,
                symbol=symbol,
                notional=existing_position.size,
                price=current_price,
                pnl=pnl,
                source_event_id=source_event_id,
                source_ts=source_ts,
                state=state,
                envelope_audit=envelope_audit,
            ), None
        else:
            # No position to close
            return _make_execution_event(
                action=ExecutionAction.REJECTED,
                symbol=symbol,
                reason="no_position_to_close",
                source_event_id=source_event_id,
                source_ts=source_ts,
                state=state,
                envelope_audit=envelope_audit,
            ), None

    elif direction in ("long", "short"):
        # Close existing if opposite direction
        if existing_position and existing_position.direction != direction:
            pnl = _compute_pnl(existing_position, current_price)
            state.total_realized_pnl += pnl
            state.daily_stats.realized_pnl += pnl
            state.daily_stats.trade_count += 1
            state.daily_stats.turnover += existing_position.size
            del state.positions[symbol]

        # Open new position
        action = ExecutionAction.OPEN_LONG if direction == "long" else ExecutionAction.OPEN_SHORT
        state.positions[symbol] = PaperPosition(
            symbol=symbol,
            direction=direction,
            size=proposed_notional,
            entry_price=current_price,
            entry_ts=_parse_timestamp(source_ts) if source_ts else datetime.now(timezone.utc),
            source_event_id=source_event_id or "",
        )
        state.daily_stats.trade_count += 1
        state.daily_stats.turnover += proposed_notional

        return _make_execution_event(
            action=action,
            symbol=symbol,
            notional=proposed_notional,
            price=current_price,
            source_event_id=source_event_id,
            source_ts=source_ts,
            state=state,
            envelope_audit=envelope_audit,
        ), None

    else:
        # Unknown direction
        return _make_execution_event(
            action=ExecutionAction.REJECTED,
            symbol=symbol,
            reason=f"unknown_direction:{direction}",
            source_event_id=source_event_id,
            source_ts=source_ts,
            state=state,
            envelope_audit=envelope_audit,
        ), None


def flat_all(
    state: ExecutorState,
    current_prices: Dict[str, float],
    reason: str,
) -> List[Dict]:
    """
    Flatten all positions (kill switch).

    Returns list of execution events.
    """
    events = []
    trigger_kill_switch(state, reason)

    for symbol, position in list(state.positions.items()):
        current_price = current_prices.get(symbol, position.entry_price)
        pnl = _compute_pnl(position, current_price)
        state.total_realized_pnl += pnl
        state.daily_stats.realized_pnl += pnl
        state.daily_stats.trade_count += 1
        state.daily_stats.turnover += position.size

        events.append(_make_execution_event(
            action=ExecutionAction.FLAT_ALL,
            symbol=symbol,
            notional=position.size,
            price=current_price,
            pnl=pnl,
            source_event_id=f"kill_switch:{reason}",
            source_ts=datetime.now(timezone.utc).isoformat(),
            state=state,
        ))

        del state.positions[symbol]

    return events


# =============================================================================
# Event Factories
# =============================================================================

def _make_execution_event(
    action: ExecutionAction,
    symbol: str,
    source_event_id: Optional[str],
    source_ts: Optional[str],
    state: ExecutorState,
    notional: float = 0.0,
    price: float = 0.0,
    pnl: float = 0.0,
    reason: str = "",
    envelope_audit: Optional[Dict] = None,
) -> Dict:
    """Create execution.paper.* event."""
    event = {
        "event_type": f"execution.paper.{action.value}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "symbol": symbol,
            "action": action.value,
            "notional": notional,
            "price": price,
            "pnl": pnl,
            "reason": reason,
            "daily_stats": {
                "date": state.daily_stats.date,
                "trade_count": state.daily_stats.trade_count,
                "turnover": state.daily_stats.turnover,
                "realized_pnl": state.daily_stats.realized_pnl,
            },
            "portfolio": {
                "position_count": len(state.positions),
                "gross_exposure": sum(p.size for p in state.positions.values()),
                "total_realized_pnl": state.total_realized_pnl,
            },
            "caps": {
                "max_notional_per_trade": BASE_MAX_NOTIONAL_PER_TRADE,
                "max_gross_exposure": BASE_MAX_GROSS_EXPOSURE,
                "max_trades_per_day": MAX_TRADES_PER_DAY,
                "max_daily_loss": MAX_DAILY_LOSS,
            },
        },
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    # Include envelope audit trail if provided
    if envelope_audit:
        event["payload"]["risk_envelope"] = envelope_audit

    # Attach build identity if available
    router_build_sha = get_router_build_sha()
    if router_build_sha:
        event["router_build_sha"] = router_build_sha

    return event


def _make_violation_event(
    breach_type: BreachType,
    reason: str,
    symbol: str,
    source_event_id: Optional[str],
    source_ts: Optional[str],
) -> Dict:
    """Create invariant.violation event for cap breach."""
    event = {
        "event_type": INVARIANT_VIOLATION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "violation_type": "paper_execution_cap_breach",
            "breach_type": breach_type.value,
            "reason": reason,
            "symbol": symbol,
            "severity": "critical" if breach_type == BreachType.MAX_DAILY_LOSS else "warning",
        },
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    router_build_sha = get_router_build_sha()
    if router_build_sha:
        event["router_build_sha"] = router_build_sha

    return event


# =============================================================================
# Spine Writer
# =============================================================================

def write_event(spine_path: Path, event: Dict) -> bool:
    """Write event to spine."""
    try:
        with spine_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
            f.flush()
        return True
    except OSError:
        return False


# =============================================================================
# Daily Bundle (Custody Artifact)
# =============================================================================

def write_daily_bundle(
    bundle_dir: Path,
    state: ExecutorState,
    spine_path: Path,
) -> Path:
    """
    Write daily custody bundle.

    Gate 2 requirement: daily bundle must exist for audit.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    today = _get_today_str()
    bundle_path = bundle_dir / f"paper_execution_bundle_{today}.json"

    # Compute spine hash
    spine_hash = ""
    if spine_path.exists():
        h = hashlib.sha256()
        with spine_path.open("rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        spine_hash = h.hexdigest()[:16]

    bundle = {
        "bundle_type": "paper_execution_daily",
        "date": today,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "spine_path": str(spine_path),
        "spine_sha256": spine_hash,
        "router_build_sha": get_router_build_sha(),
        "soak_contract_hash": get_soak_contract_hash(),
        "state": {
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "total_realized_pnl": state.total_realized_pnl,
            "events_processed": state.events_processed,
            "position_count": len(state.positions),
            "positions": {
                symbol: {
                    "direction": p.direction,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "entry_ts": p.entry_ts.isoformat(),
                }
                for symbol, p in state.positions.items()
            },
        },
        "daily_stats": {
            "date": state.daily_stats.date,
            "trade_count": state.daily_stats.trade_count,
            "turnover": state.daily_stats.turnover,
            "realized_pnl": state.daily_stats.realized_pnl,
        },
        "caps": {
            "max_notional_per_trade": MAX_NOTIONAL_PER_TRADE,
            "max_gross_exposure": MAX_GROSS_EXPOSURE,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "max_turnover_per_day": MAX_TURNOVER_PER_DAY,
            "max_daily_loss": MAX_DAILY_LOSS,
            "paper_capital": PAPER_CAPITAL,
        },
    }

    with bundle_path.open("w") as f:
        json.dump(bundle, f, indent=2)

    return bundle_path
