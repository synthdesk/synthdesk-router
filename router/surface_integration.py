"""
Veto Surface Integration for Router

DOCTRINE: VETO_TIMESCALE

Integrates veto surfaces into the router decision path:
- Loads latest regime/micro surfaces from jsonl streams
- Evaluates intended_hold_min against surfaces
- Emits router.decision.v1 events with surface hashes attached

Evidence: EVT-0, EVT-1A, EVT-1B (gate2_final_2026-01-20)
Added: 2026-01-23
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from router.constraints import VetoReason
from router.surface_policy import (
    BlockReason,
    DecisionOutcome,
    PolicyId,
    SurfaceDecision,
    build_attached_surfaces,
    evaluate_surfaces,
    decision_to_dict,
)
from synthdesk_spine.payloads.veto_surface import (
    HorizonRisk,
    VetoSurfaceInputs,
    VetoSurfaceIntegrity,
    VetoSurfacePayload,
)

logger = logging.getLogger(__name__)


def _parse_horizon_risk(data: Dict[str, Any]) -> HorizonRisk:
    """Parse HorizonRisk from dict."""
    return HorizonRisk(
        status=data["status"],
        net_bps=data.get("net_bps"),
        cvar10_bps=data.get("cvar10_bps"),
        elv_bps=data.get("elv_bps"),
        conf=data.get("conf"),
    )


def _parse_inputs(data: Dict[str, Any]) -> VetoSurfaceInputs:
    """Parse VetoSurfaceInputs from dict."""
    return VetoSurfaceInputs(
        evidence_refs=tuple(data["evidence_refs"]),
        input_hash=data["input_hash"],
        params_hash=data["params_hash"],
        code_sha=data["code_sha"],
    )


def _parse_integrity(data: Optional[Dict[str, Any]]) -> Optional[VetoSurfaceIntegrity]:
    """Parse VetoSurfaceIntegrity from dict."""
    if data is None:
        return None
    return VetoSurfaceIntegrity(
        event_hash=data["event_hash"],
        prev_event_hash=data.get("prev_event_hash"),
    )


def parse_surface_from_event(event: Dict[str, Any]) -> VetoSurfacePayload:
    """
    Parse a VetoSurfacePayload from a spine event dict.

    Args:
        event: Event dict with payload containing surface data

    Returns:
        VetoSurfacePayload

    Raises:
        KeyError: If required fields are missing
        ValueError: If payload validation fails
    """
    payload = event["payload"]

    # Parse risk_by_horizon
    risk_by_horizon = {
        k: _parse_horizon_risk(v)
        for k, v in payload.get("risk_by_horizon", {}).items()
    }

    return VetoSurfacePayload(
        asset=payload["asset"],
        plane=payload["plane"],
        schema_version=payload["schema_version"],
        status=payload["status"],
        horizons_min=tuple(payload["horizons_min"]),
        turn_on_min=payload.get("turn_on_min"),
        unsafe_up_to_min=payload.get("unsafe_up_to_min"),
        risk_by_horizon=risk_by_horizon,
        reasons=payload["reasons"],
        inputs=_parse_inputs(payload["inputs"]),
        integrity=_parse_integrity(payload.get("integrity")),
    )


def load_latest_surface(
    stream_path: Path,
    asset: str,
    plane: str,
    max_lines: int = 1000,
) -> Optional[VetoSurfacePayload]:
    """
    Load the latest surface for an asset+plane from a jsonl stream.

    Scans backward from end of file for efficiency.

    Args:
        stream_path: Path to surface events jsonl
        asset: Asset symbol to find
        plane: Plane to find ("regime" or "micro")
        max_lines: Maximum lines to scan backward

    Returns:
        Latest matching VetoSurfacePayload, or None if not found
    """
    surface, _ = load_latest_surface_with_timestamp(stream_path, asset, plane, max_lines)
    return surface


def load_latest_surface_with_timestamp(
    stream_path: Path,
    asset: str,
    plane: str,
    max_lines: int = 1000,
) -> Tuple[Optional[VetoSurfacePayload], Optional[datetime]]:
    """
    Load the latest surface for an asset+plane from a jsonl stream.

    Returns both the surface and its EVENT timestamp for staleness checking.
    Staleness is based on event timestamp, NOT file mtime.

    Args:
        stream_path: Path to surface events jsonl (LIVE stream)
        asset: Asset symbol to find
        plane: Plane to find ("regime" or "micro")
        max_lines: Maximum lines to scan backward

    Returns:
        (surface, event_timestamp) - either may be None if not found
    """
    if not stream_path.exists():
        logger.debug(f"Surface stream not found: {stream_path}")
        return (None, None)

    # Read last N lines (simple approach for now)
    try:
        with stream_path.open("r") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning(f"Failed to read surface stream: {e}")
        return (None, None)

    # Scan backward for matching surface
    for line in reversed(lines[-max_lines:]):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            payload = event.get("payload", {})
            if payload.get("asset") == asset and payload.get("plane") == plane:
                surface = parse_surface_from_event(event)
                # Extract event timestamp for staleness checking
                event_ts = None
                ts_str = event.get("ts_utc")
                if ts_str:
                    try:
                        event_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        logger.debug(f"Failed to parse event timestamp: {ts_str}")
                return (surface, event_ts)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to parse surface event: {e}")
            continue

    return (None, None)


class SurfaceCache:
    """
    In-memory cache for veto surfaces.

    Periodically reloaded from jsonl streams.
    Applies timestamp-based staleness checking (not file mtime).
    """

    # Default staleness threshold: surface older than this is STALE
    DEFAULT_STALE_SECONDS = 600  # 10 minutes

    def __init__(
        self,
        regime_stream_path: Optional[Path] = None,
        micro_stream_path: Optional[Path] = None,
        ttl_seconds: float = 300,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
    ):
        """
        Initialize the surface cache.

        Args:
            regime_stream_path: Path to regime surface events jsonl (LIVE stream)
            micro_stream_path: Path to micro surface events jsonl (LIVE stream)
            ttl_seconds: Cache TTL (reload surfaces after this duration)
            stale_seconds: Surface staleness threshold (based on event timestamp)
        """
        self.regime_stream_path = regime_stream_path
        self.micro_stream_path = micro_stream_path
        self.ttl_seconds = ttl_seconds
        self.stale_seconds = stale_seconds

        # Cache: (asset, plane) -> (surface, event_timestamp, load_time)
        self._cache: Dict[Tuple[str, str], Tuple[VetoSurfacePayload, Optional[datetime], float]] = {}

    def get_surfaces(
        self,
        asset: str,
    ) -> Tuple[Optional[VetoSurfacePayload], Optional[VetoSurfacePayload]]:
        """
        Get cached or freshly loaded surfaces for an asset.

        NOTE: Returns raw surfaces. Staleness checking happens in evaluate_surface_gate.

        Returns:
            (regime_surface, micro_surface) - either may be None
        """
        now = datetime.now(timezone.utc).timestamp()

        regime = self._get_cached_or_load(asset, "regime", now)
        micro = self._get_cached_or_load(asset, "micro", now)

        return (regime, micro)

    def is_surface_stale(self, asset: str, plane: str) -> bool:
        """
        Check if a cached surface is stale based on EVENT timestamp.

        This is timestamp-based staleness, not file mtime.

        Args:
            asset: Asset symbol
            plane: Plane ("regime" or "micro")

        Returns:
            True if surface is stale or missing
        """
        key = (asset, plane)
        if key not in self._cache:
            return True

        _, event_ts, _ = self._cache[key]
        if event_ts is None:
            return True

        now = datetime.now(timezone.utc)
        age_seconds = (now - event_ts).total_seconds()
        return age_seconds > self.stale_seconds

    def _get_cached_or_load(
        self,
        asset: str,
        plane: str,
        now: float,
    ) -> Optional[VetoSurfacePayload]:
        """Get from cache if fresh, else reload."""
        key = (asset, plane)

        if key in self._cache:
            surface, event_ts, load_time = self._cache[key]
            if now - load_time < self.ttl_seconds:
                return surface

        # Reload from live stream
        stream_path = (
            self.regime_stream_path if plane == "regime" else self.micro_stream_path
        )
        if stream_path is None:
            return None

        surface, event_ts = load_latest_surface_with_timestamp(stream_path, asset, plane)
        if surface is not None:
            self._cache[key] = (surface, event_ts, now)

        return surface

    def invalidate(self, asset: Optional[str] = None, plane: Optional[str] = None):
        """
        Invalidate cache entries.

        Args:
            asset: Invalidate only this asset (None = all)
            plane: Invalidate only this plane (None = all)
        """
        if asset is None and plane is None:
            self._cache.clear()
            return

        keys_to_remove = [
            k for k in self._cache
            if (asset is None or k[0] == asset) and (plane is None or k[1] == plane)
        ]
        for k in keys_to_remove:
            del self._cache[k]


def evaluate_surface_gate(
    asset: str,
    intended_hold_min: Optional[int],
    regime_surface: Optional[VetoSurfacePayload],
    micro_surface: Optional[VetoSurfacePayload],
    policy_id: PolicyId = PolicyId.P_HOLD_BLOCK_REGIME,
) -> Tuple[bool, Optional[VetoReason], SurfaceDecision]:
    """
    Evaluate surfaces and return gate decision.

    Integrates surface_policy with router's VetoReason system.

    NOTE on micro NO_DATA: Micro surfaces with status=NO_DATA are NOT a veto
    condition by design. They represent informational uncertainty (no orderbook
    evidence admitted yet), not danger. This is intentional asymmetry with
    regime surfaces, which fail-closed on NO_DATA. Do not "safety refactor"
    this into a block without constitutional amendment.

    Args:
        asset: Trading pair symbol
        intended_hold_min: Proposed holding period in minutes (REQUIRED)
        regime_surface: Regime plane veto surface (or None)
        micro_surface: Micro plane veto surface (or None)
        policy_id: Which policy to apply

    Returns:
        (allowed, veto_reason, decision)
        - allowed: True if decision allows action
        - veto_reason: VetoReason if blocked, None if allowed
        - decision: Full SurfaceDecision for audit trail

    Raises:
        ValueError: If intended_hold_min is invalid
    """
    if intended_hold_min is None or not isinstance(intended_hold_min, int) or intended_hold_min <= 0:
        decision = _missing_hold_decision(
            asset,
            policy_id,
            policy_defaulted=False,
            regime_surface=regime_surface,
            micro_surface=micro_surface,
        )
        return (False, VetoReason.SURFACE_MISSING_HOLD, decision)

    decision = evaluate_surfaces(
        asset=asset,
        intended_hold_min=intended_hold_min,
        regime_surface=regime_surface,
        micro_surface=micro_surface,
        policy_id=policy_id,
        policy_defaulted=False,
    )

    if decision.allowed:
        return (True, None, decision)

    # Map block reasons to VetoReason
    # Order matters: first block determines the veto reason
    for block in decision.blocks:
        if block.plane == "regime":
            # Distinguish: MISSING vs STALE/ERROR/NO_DATA vs active block
            if "MISSING" in block.code:
                return (False, VetoReason.SURFACE_REGIME_MISSING, decision)
            elif "STALE" in block.code or "ERROR" in block.code or "NO_DATA" in block.code:
                return (False, VetoReason.SURFACE_REGIME_STALE, decision)
            else:
                # Active block: HOLD_GE_TURN_ON_*
                return (False, VetoReason.SURFACE_REGIME_BLOCK, decision)
        elif block.plane == "micro":
            # Distinguish: MISSING vs STALE/ERROR vs active block
            # NOTE: Micro NO_DATA never blocks (annotate only), so no NO_DATA case here
            if "MISSING" in block.code or "STALE" in block.code or "ERROR" in block.code:
                return (False, VetoReason.SURFACE_MICRO_STALE, decision)
            else:
                # Active block: SCALP_LE_UNSAFE_*
                return (False, VetoReason.SURFACE_MICRO_BLOCK, decision)

    # Fallback (shouldn't reach here - all blocks have a plane)
    # Log warning for forensic visibility if this path is ever hit
    logger.warning(
        f"evaluate_surface_gate fallback hit: asset={asset}, "
        f"blocks={[b.code for b in decision.blocks]}"
    )
    return (False, VetoReason.SURFACE_REGIME_BLOCK, decision)


def make_decision_event_payload(
    decision: SurfaceDecision,
    source_ts: str,
    source_event_id: str,
) -> Dict[str, Any]:
    """
    Create payload for router.decision.v1 event.

    This payload attaches surface hashes for audit/replay.

    Args:
        decision: SurfaceDecision from policy evaluation
        source_ts: Source event timestamp
        source_event_id: Source event ID

    Returns:
        Payload dict for router.decision.v1
    """
    decision_dict = decision_to_dict(decision)
    decision_dict["source_ts"] = source_ts
    decision_dict["source_event_id"] = source_event_id
    decision_dict["schema_version"] = "1.0"

    return decision_dict


def _missing_hold_decision(
    asset: str,
    policy_id: PolicyId,
    *,
    policy_defaulted: bool,
    regime_surface: Optional[VetoSurfacePayload],
    micro_surface: Optional[VetoSurfacePayload],
) -> SurfaceDecision:
    """
    Build a SurfaceDecision when intended_hold_min is missing.

    This is a system-level veto that still logs surface presence for auditing.
    """
    attached_surfaces = build_attached_surfaces(regime_surface, micro_surface)

    block_reason = BlockReason(
        plane=None,
        code="MISSING_HOLD",
        policy_id=policy_id.value,
        threshold_min=None,
        intended_hold_min=None,
        surface_event_hash=None,
    )

    return SurfaceDecision(
        asset=asset,
        intended_hold_min=None,
        policy_id=policy_id.value,
        policy_defaulted=policy_defaulted,
        outcome=DecisionOutcome.BLOCK,
        blocks=[block_reason],
        annotations=["MISSING_HOLD"],
        attached_surfaces=attached_surfaces,
        hold_used=None,
        hold_required=True,
    )
