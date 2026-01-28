#!/usr/bin/env python3
"""
Synthetic Micro Surface Emitter v0

DOCTRINE: VETO_TIMESCALE (Amendment: synthetic_micro_v0_from_regime)

Converts market.regime events into risk.veto_surface.micro.v1 events using
volatility/range proxies from the regime classifier as micro risk signals.

This is a SYNTHETIC approximation. True micro surfaces require orderbook admission
(not yet available). This emitter provides observability + classification data
until real micro surfaces are possible.

Contract:
- Input: market.regime events from spine jsonl
- Output: append risk.veto_surface.micro.v1 to micro.jsonl
- Cadence: event-driven (tail/follow spine)
- Idempotent: dedup by source_event_id

Micro semantics (CRITICAL - inverted from regime):
- Regime blocks LONG holds: intended_hold_min >= turn_on_min
- Micro blocks SHORT holds: intended_hold_min <= unsafe_up_to_min

Risk thresholds (v0, from ACTIVATE_synthetic_micro_v0_from_regime):
- Strong risk (unsafe_up_to_min=5): z_std > VOL_MAX OR range_norm > RANGE_MAX
- Moderate risk (unsafe_up_to_min=3): z_std > VOL_MAX*0.7 OR jump detected
- No risk (unsafe_up_to_min=None): conditions clear

Constants (FROZEN - require court amendment to change):
- VOL_MAX = 0.020
- RANGE_MAX = 0.015
- JUMP_K = 4.0 (z_mean jump detection multiplier)

Created: 2026-01-24
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

# Add packages to path for spine_sdk import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from synthdesk_spine.event_types import RISK_VETO_SURFACE_MICRO_V1

# Frozen constants (require court amendment)
VOL_MAX = 0.020
RANGE_MAX = 0.015
JUMP_K = 4.0
MICRO_HORIZONS = (3, 5, 10)  # Micro horizon grid
CODE_SHA = "micro_emitter_synth_v0.1"


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
    for field in ["event_id", "id", "source_event_id"]:
        if evt.get(field):
            return evt[field]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def extract_asset(payload: Dict) -> Optional[str]:
    """Extract asset symbol from payload."""
    for field in ["symbol", "asset", "pair"]:
        if payload.get(field):
            return payload[field]
    return None


class MicroRiskAssessor:
    """
    Assess micro risk from market.regime metrics using 60-tick window.

    Synthetic approximation using:
    - z_std: volatility proxy
    - range_norm: range compression/expansion
    - z_mean: directional drift (for jump detection)
    """

    def __init__(self, window_size: int = 60):
        self.window_size = window_size
        # Per-asset rolling windows: asset -> deque of (ts, z_mean, z_std, range_norm)
        self._windows: Dict[str, Deque[Tuple[float, float, float, float]]] = {}

    def update(
        self,
        asset: str,
        ts: float,
        z_mean: Optional[float],
        z_std: Optional[float],
        range_norm: Optional[float],
    ) -> None:
        """Update rolling window with new observation."""
        if asset not in self._windows:
            self._windows[asset] = deque(maxlen=self.window_size)

        # Store observation (use 0.0 for None to maintain window structure)
        self._windows[asset].append((
            ts,
            z_mean if z_mean is not None else 0.0,
            z_std if z_std is not None else 0.0,
            range_norm if range_norm is not None else 0.0,
        ))

    def assess(self, asset: str) -> Tuple[Optional[int], str, List[str]]:
        """
        Assess micro risk for asset.

        Returns:
            (unsafe_up_to_min, status, reasons)
            - unsafe_up_to_min: 5 (strong), 3 (moderate), None (clear)
            - status: "ACTIVE" or "NO_DATA"
            - reasons: list of reason codes
        """
        if asset not in self._windows or len(self._windows[asset]) < 10:
            # Insufficient data for assessment
            return (None, "NO_DATA", ["INSUFFICIENT_TICKS"])

        window = self._windows[asset]
        reasons: List[str] = []

        # Get latest values
        _, latest_z_mean, latest_z_std, latest_range = window[-1]

        # Compute window statistics for jump detection
        z_means = [obs[1] for obs in window]
        z_stds = [obs[2] for obs in window]
        ranges = [obs[3] for obs in window]

        # Mean and std of z_mean for jump detection
        mean_z_mean = sum(z_means) / len(z_means) if z_means else 0.0
        std_z_mean = (sum((x - mean_z_mean) ** 2 for x in z_means) / len(z_means)) ** 0.5 if len(z_means) > 1 else 0.0

        # Jump detection: |latest_z_mean - mean| > JUMP_K * std
        is_jump = False
        if std_z_mean > 1e-10:
            deviation = abs(latest_z_mean - mean_z_mean)
            if deviation > JUMP_K * std_z_mean:
                is_jump = True
                reasons.append(f"JUMP_DETECTED_K{JUMP_K:.1f}")

        # Strong risk conditions
        vol_high = latest_z_std > VOL_MAX
        range_high = latest_range > RANGE_MAX

        if vol_high:
            reasons.append(f"VOL_HIGH_{latest_z_std:.4f}>{VOL_MAX}")
        if range_high:
            reasons.append(f"RANGE_HIGH_{latest_range:.4f}>{RANGE_MAX}")

        # Moderate risk conditions
        vol_moderate = latest_z_std > VOL_MAX * 0.7
        if vol_moderate and not vol_high:
            reasons.append(f"VOL_MODERATE_{latest_z_std:.4f}>{VOL_MAX * 0.7:.4f}")

        # Determine unsafe_up_to_min
        if vol_high or range_high:
            # Strong risk: block scalps up to 5 minutes
            return (5, "ACTIVE", reasons or ["STRONG_RISK"])
        elif vol_moderate or is_jump:
            # Moderate risk: block scalps up to 3 minutes
            return (3, "ACTIVE", reasons or ["MODERATE_RISK"])
        else:
            # Clear: no micro block
            reasons.append("CONDITIONS_CLEAR")
            return (None, "ACTIVE", reasons)

    def get_latest_metrics(self, asset: str) -> Dict[str, Optional[float]]:
        """Get latest metrics for audit trail."""
        if asset not in self._windows or len(self._windows[asset]) == 0:
            return {"z_mean": None, "z_std": None, "range_norm": None}
        _, z_mean, z_std, range_norm = self._windows[asset][-1]
        return {
            "z_mean": z_mean,
            "z_std": z_std,
            "range_norm": range_norm,
        }


def build_micro_surface_event(
    asset: str,
    status: str,
    unsafe_up_to_min: Optional[int],
    reasons: List[str],
    source_event_id: str,
    source_ts: str,
    metrics: Dict[str, Optional[float]],
    stale_after_s: int,
) -> Dict:
    """
    Build risk.veto_surface.micro.v1 event.

    CRITICAL: Micro uses unsafe_up_to_min, NOT turn_on_min.
    turn_on_min MUST be None for micro surfaces.
    """
    # Check staleness
    try:
        age_s = time.time() - parse_ts(source_ts)
    except (ValueError, TypeError):
        age_s = float("inf")

    if status == "ACTIVE" and age_s > stale_after_s:
        status = "STALE"
        reasons = ["SOURCE_STALE"] + reasons

    # Build risk_by_horizon (empty for synthetic v0 - we don't have per-horizon stats)
    risk_by_horizon = {}

    # For ACTIVE status with metrics, populate minimal info
    if status == "ACTIVE" and metrics.get("z_std") is not None:
        # Use latest z_std as a rough CVaR proxy (normalized)
        z_std = metrics.get("z_std", 0.0) or 0.0
        for h in MICRO_HORIZONS:
            h_key = str(h)
            risk_by_horizon[h_key] = {
                "status": "UNKNOWN",  # Synthetic - no true dominance calc
                "net_bps": None,
                "cvar10_bps": -z_std * 10000 if z_std else None,  # Rough proxy
                "elv_bps": None,
                "conf": None,
            }

    # NO_DATA surfaces must have empty risk_by_horizon
    if status == "NO_DATA":
        risk_by_horizon = {}

    return {
        "event_type": RISK_VETO_SURFACE_MICRO_V1,
        "ts_utc": utc_now_iso(),
        "payload": {
            "asset": asset,
            "plane": "micro",
            "schema_version": "1.0",
            "status": status,
            "horizons_min": list(MICRO_HORIZONS),
            "turn_on_min": None,  # MUST be None for micro (regime semantic)
            "unsafe_up_to_min": unsafe_up_to_min,  # MICRO semantic
            "risk_by_horizon": risk_by_horizon,
            "reasons": {
                "regime": [],  # Empty - this is micro
                "micro": reasons,
            },
            "inputs": {
                "evidence_refs": [source_event_id],
                "input_hash": compute_event_hash({"source": source_event_id, "metrics": metrics}),
                "params_hash": compute_event_hash({
                    "VOL_MAX": VOL_MAX,
                    "RANGE_MAX": RANGE_MAX,
                    "JUMP_K": JUMP_K,
                }),
                "code_sha": CODE_SHA,
            },
            "integrity": {
                "event_hash": compute_event_hash({"source_event_id": source_event_id, "ts": source_ts}),
                "prev_event_hash": None,
            },
            # Extra audit fields (not part of schema, but useful)
            "source_event_id": source_event_id,
            "source_ts": source_ts,
            "synthetic_version": "v0",
            "metrics_snapshot": metrics,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Synthetic Micro Surface Emitter v0")
    ap.add_argument("--spine", required=True, help="Path to event spine jsonl")
    ap.add_argument("--out", required=True, help="Output path for micro.jsonl")
    ap.add_argument("--stale-after-s", type=int, default=180,
                    help="Seconds after which surface is considered stale")
    ap.add_argument("--poll", type=float, default=0.25,
                    help="Poll interval in seconds")
    ap.add_argument("--dedup",
                    default="/root/synthdesk-router/soak_artifacts/veto_surfaces/micro_emitter_dedup.txt",
                    help="Path to dedup file")
    ap.add_argument("--asset-filter", default="",
                    help="Comma-separated list of assets to include (empty = all)")
    ap.add_argument("--window-size", type=int, default=60,
                    help="Rolling window size for risk assessment (ticks)")
    args = ap.parse_args()

    # Parse asset filter
    asset_allow = None
    if args.asset_filter:
        asset_allow = set(a.strip() for a in args.asset_filter.split(",") if a.strip())

    # Load dedup state
    seen = load_dedup(args.dedup)
    print(f"[micro_surface_emitter] Loaded {len(seen)} seen event IDs", flush=True)
    print(f"[micro_surface_emitter] Tailing {args.spine}", flush=True)
    print(f"[micro_surface_emitter] Output: {args.out}", flush=True)
    print(f"[micro_surface_emitter] Constants: VOL_MAX={VOL_MAX}, RANGE_MAX={RANGE_MAX}, JUMP_K={JUMP_K}", flush=True)
    print(f"[micro_surface_emitter] Window size: {args.window_size} ticks", flush=True)

    # Initialize risk assessor
    assessor = MicroRiskAssessor(window_size=args.window_size)
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

        # Extract metrics
        source_ts = evt.get("ts") or payload.get("ts") or utc_now_iso()
        metrics = payload.get("metrics") or {}
        phase1 = payload.get("phase1") or {}

        z_mean = metrics.get("z_mean")
        z_std = metrics.get("z_std")
        range_norm = phase1.get("range_norm")

        # Parse timestamp for window
        try:
            ts_unix = parse_ts(source_ts)
        except (ValueError, TypeError):
            ts_unix = time.time()

        # Update risk assessor
        assessor.update(asset, ts_unix, z_mean, z_std, range_norm)

        # Assess risk
        unsafe_up_to_min, status, reasons = assessor.assess(asset)
        latest_metrics = assessor.get_latest_metrics(asset)

        # Build and emit surface event
        out_evt = build_micro_surface_event(
            asset=asset,
            status=status,
            unsafe_up_to_min=unsafe_up_to_min,
            reasons=reasons,
            source_event_id=source_event_id,
            source_ts=source_ts,
            metrics=latest_metrics,
            stale_after_s=args.stale_after_s,
        )

        emit(args.out, out_evt)
        emitted_count += 1

        # Update dedup
        seen.add(source_event_id)
        append_dedup(args.dedup, source_event_id)

        if emitted_count % 100 == 0:
            print(
                f"[micro_surface_emitter] Emitted {emitted_count} surfaces "
                f"(latest: {asset} status={status} unsafe_up_to={unsafe_up_to_min})",
                flush=True,
            )


if __name__ == "__main__":
    main()
