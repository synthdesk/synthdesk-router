"""
Cross-asset global throttle (allocator adjunct).

Design goal:
- if enabled, provide a single scalar throttle in [0,1] plus rationale+metadata.
- if disabled (default), be a pure no-op.

This is an epistemic safety control, not PnL optimization.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _env_flag(name: str, default: str) -> bool:
    raw = os.environ.get(name, default)
    if raw is None:
        return False
    value = str(raw).strip().lower()
    return value not in ("0", "false", "no", "off", "")


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class CrossAssetConfig:
    enabled: bool = False
    shadow_mode: bool = True

    # Optional json file containing throttle state.
    throttle_path: Optional[Path] = None

    # Staleness bound for throttle artifact.
    max_staleness_s: int = 3600

    # Used when enabled but artifact is missing/unreadable (shadow_mode gates behavior).
    default_throttle: float = 1.0


def load_cross_asset_config_from_env() -> CrossAssetConfig:
    enabled = _env_flag("ROUTER_CROSS_ASSET_ENABLED", "0")
    shadow_mode = _env_flag("ROUTER_CROSS_ASSET_SHADOW_MODE", "1")

    raw_path = os.environ.get("ROUTER_CROSS_ASSET_THROTTLE_PATH")
    throttle_path = Path(raw_path).expanduser().resolve() if raw_path else None

    try:
        max_staleness_s = int(os.environ.get("ROUTER_CROSS_ASSET_MAX_STALENESS_S", "3600"))
    except Exception:
        max_staleness_s = 3600

    try:
        default_throttle = float(os.environ.get("ROUTER_CROSS_ASSET_DEFAULT_THROTTLE", "1.0"))
    except Exception:
        default_throttle = 1.0

    return CrossAssetConfig(
        enabled=enabled,
        shadow_mode=shadow_mode,
        throttle_path=throttle_path,
        max_staleness_s=max_staleness_s,
        default_throttle=_clamp01(default_throttle),
    )


def get_global_throttle(config: CrossAssetConfig, now: datetime) -> Tuple[float, str, Dict[str, Any]]:
    """
    Returns: (throttle, rationale, metadata)

    Fail-closed semantics when enabled:
      - shadow_mode=True  -> degrade to 1.0 (log-only)
      - shadow_mode=False -> degrade to 0.0 (global risk-off)
    """
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)

    meta: Dict[str, Any] = {
        "enabled": config.enabled,
        "shadow_mode": config.shadow_mode,
        "path": str(config.throttle_path) if config.throttle_path else None,
        "max_staleness_s": config.max_staleness_s,
    }

    if not config.enabled:
        return 1.0, "global_throttle:disabled", meta

    # Missing path/file.
    if not config.throttle_path or not config.throttle_path.exists():
        meta["reason"] = "missing_throttle_path"
        if config.shadow_mode:
            return 1.0, "global_throttle:missing(shadow)", meta
        return 0.0, "global_throttle:missing", meta

    # Read artifact.
    try:
        payload = json.loads(config.throttle_path.read_text(encoding="utf-8"))
    except Exception as exc:
        meta["reason"] = f"read_error:{type(exc).__name__}"
        if config.shadow_mode:
            return 1.0, "global_throttle:read_error(shadow)", meta
        return 0.0, "global_throttle:read_error", meta

    # Accept a few key variants.
    throttle_raw = payload.get("throttle", payload.get("global_throttle", payload.get("risk_throttle")))
    reason = payload.get("reason", payload.get("rationale", "unspecified"))
    ts = payload.get("ts_utc", payload.get("timestamp"))

    ts_dt = _parse_ts(ts)
    if ts_dt is None:
        meta["reason"] = "missing_or_invalid_ts"
        if config.shadow_mode:
            return 1.0, "global_throttle:bad_ts(shadow)", meta
        return 0.0, "global_throttle:bad_ts", meta

    age_s = (now_utc - ts_dt).total_seconds()
    meta["ts_utc"] = ts_dt.isoformat().replace("+00:00", "Z")
    meta["age_s"] = age_s
    meta["artifact_reason"] = reason

    if age_s > config.max_staleness_s:
        meta["reason"] = "stale"
        if config.shadow_mode:
            return 1.0, "global_throttle:stale(shadow)", meta
        return 0.0, "global_throttle:stale", meta

    try:
        throttle = _clamp01(float(throttle_raw))
    except Exception:
        meta["reason"] = "invalid_throttle"
        if config.shadow_mode:
            return 1.0, "global_throttle:bad_value(shadow)", meta
        return 0.0, "global_throttle:bad_value", meta

    meta["throttle"] = throttle
    return throttle, f"global_throttle={throttle:.2f} ({reason})", meta
