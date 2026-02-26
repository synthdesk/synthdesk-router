"""
Horizon-aware sizing adjunct (allocator).

Design goals:
- optional horizon metadata and size adjustment based on epistemic state.
- disabled by default to preserve legacy behavior.
- graceful degradation when artifacts are absent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _env_flag(name: str, default: str) -> bool:
    raw = os.environ.get(name, default)
    if raw is None:
        return False
    value = str(raw).strip().lower()
    return value not in ("0", "false", "no", "off", "")


def _safe_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    return Path(raw_path).expanduser().resolve()


def _clamp_nonnegative(value: float) -> float:
    if value < 0.0:
        return 0.0
    return value


def _clamp_01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


@dataclass(frozen=True)
class HorizonConfig:
    enabled: bool = False
    shadow_mode: bool = True
    index_path: Optional[Path] = None
    default_horizon_min: int = 15
    default_multiplier: float = 1.0


def load_horizon_config_from_env() -> HorizonConfig:
    enabled = _env_flag("ROUTER_HORIZON_ENABLED", "0")
    shadow_mode = _env_flag("ROUTER_HORIZON_SHADOW_MODE", "1")
    index_path = _safe_path(os.environ.get("ROUTER_HORIZON_INDEX_PATH"))

    try:
        default_horizon_min = int(os.environ.get("ROUTER_HORIZON_DEFAULT_MIN", "15"))
    except Exception:
        default_horizon_min = 15

    try:
        default_multiplier = float(os.environ.get("ROUTER_HORIZON_DEFAULT_MULTIPLIER", "1.0"))
    except Exception:
        default_multiplier = 1.0

    return HorizonConfig(
        enabled=enabled,
        shadow_mode=shadow_mode,
        index_path=index_path,
        default_horizon_min=max(1, default_horizon_min),
        default_multiplier=_clamp_01(default_multiplier),
    )


def load_horizon_index(config: HorizonConfig) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not config.enabled:
        return None, "disabled"
    if config.index_path is None:
        return None, "missing_horizon_index_path"
    if not config.index_path.exists():
        return None, "horizon_index_not_found"

    try:
        payload = json.loads(config.index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"horizon_index_read_error:{type(exc).__name__}"

    if not isinstance(payload, dict):
        return None, "horizon_index_invalid_payload"

    return payload, None


class HorizonAwareSizer:
    def __init__(self, index: Optional[Dict[str, Any]], config: HorizonConfig):
        self.index = index if isinstance(index, dict) else {}
        self.config = config

    def _asset_entry(self, asset: str) -> Dict[str, Any]:
        assets = self.index.get("assets")
        if isinstance(assets, dict):
            candidate = assets.get(asset) or assets.get(asset.upper())
            if isinstance(candidate, dict):
                return candidate

        candidate = self.index.get(asset) or self.index.get(asset.upper())
        if isinstance(candidate, dict):
            return candidate

        return {}

    def _resolve_horizon(self, entry: Dict[str, Any], epistemic_state: str) -> Tuple[int, str]:
        state_key = epistemic_state.upper()

        by_state = entry.get("horizon_by_state")
        if isinstance(by_state, dict) and state_key in by_state:
            try:
                return max(1, int(by_state[state_key])), "asset_state"
            except Exception:
                pass

        global_by_state = self.index.get("horizon_by_state")
        if isinstance(global_by_state, dict) and state_key in global_by_state:
            try:
                return max(1, int(global_by_state[state_key])), "global_state"
            except Exception:
                pass

        if "horizon_min" in entry:
            try:
                return max(1, int(entry["horizon_min"])), "asset"
            except Exception:
                pass

        if "default_horizon_min" in self.index:
            try:
                return max(1, int(self.index["default_horizon_min"])), "global_default"
            except Exception:
                pass

        return self.config.default_horizon_min, "config_default"

    def _resolve_multiplier(self, entry: Dict[str, Any], epistemic_state: str) -> Tuple[float, str]:
        state_key = epistemic_state.upper()

        by_state = entry.get("multiplier_by_state")
        if isinstance(by_state, dict) and state_key in by_state:
            try:
                return _clamp_01(float(by_state[state_key])), "asset_state"
            except Exception:
                pass

        global_by_state = self.index.get("multiplier_by_state")
        if isinstance(global_by_state, dict) and state_key in global_by_state:
            try:
                return _clamp_01(float(global_by_state[state_key])), "global_state"
            except Exception:
                pass

        if "multiplier" in entry:
            try:
                return _clamp_01(float(entry["multiplier"])), "asset"
            except Exception:
                pass

        if "default_multiplier" in self.index:
            try:
                return _clamp_01(float(self.index["default_multiplier"])), "global_default"
            except Exception:
                pass

        return self.config.default_multiplier, "config_default"

    def size(
        self,
        asset: str,
        epistemic_state: str,
        base_size: float,
    ) -> Tuple[float, Dict[str, Any]]:
        base_size = _clamp_nonnegative(float(base_size))
        state = (epistemic_state or "INTERIOR").upper()
        entry = self._asset_entry(asset)

        horizon_used, horizon_source = self._resolve_horizon(entry, state)
        multiplier, multiplier_source = self._resolve_multiplier(entry, state)
        adjusted_size = _clamp_nonnegative(base_size * multiplier)

        meta = {
            "asset": asset,
            "epistemic_state": state,
            "horizon_used": horizon_used,
            "horizon_source": horizon_source,
            "multiplier": multiplier,
            "multiplier_source": multiplier_source,
            "base_size": base_size,
            "adjusted_size": adjusted_size,
            "enabled": self.config.enabled,
            "shadow_mode": self.config.shadow_mode,
        }

        return adjusted_size, meta
