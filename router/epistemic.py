"""
Epistemic sizing adjunct (allocator).

Design goals:
- provide optional participation-quality discounting when explicitly enabled.
- default to disabled/no-op behavior so legacy runtime remains unchanged.
- fail-safe semantics: shadow mode logs without applying size changes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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


def _safe_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    return Path(raw_path).expanduser().resolve()


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class EpistemicConfig:
    enabled: bool = False
    shadow_mode: bool = True
    pq_path: Optional[Path] = None
    pq_dir: Optional[Path] = None
    kernel_multipliers_path: Optional[Path] = None
    max_staleness_s: int = 3600
    min_participation: float = 0.0
    default_discount: float = 1.0
    use_kernel_multipliers: bool = False


@dataclass(frozen=True)
class ParticipationQuality:
    score: float
    ts_utc: Optional[datetime] = None
    state_distribution: Dict[str, float] = field(default_factory=dict)
    source: Optional[str] = None


@dataclass(frozen=True)
class KernelMultipliers:
    global_multiplier: float = 1.0
    by_regime: Dict[str, float] = field(default_factory=dict)
    by_state: Dict[str, float] = field(default_factory=dict)
    by_regime_state: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def lookup(self, regime: Optional[str], state: Optional[str]) -> float:
        regime_key = (regime or "").strip().lower()
        state_key = (state or "").strip().upper()

        if regime_key and state_key:
            regime_bucket = self.by_regime_state.get(regime_key)
            if isinstance(regime_bucket, dict):
                if state_key in regime_bucket:
                    return _clamp01(float(regime_bucket[state_key]))

        if regime_key and regime_key in self.by_regime:
            return _clamp01(float(self.by_regime[regime_key]))

        if state_key and state_key in self.by_state:
            return _clamp01(float(self.by_state[state_key]))

        return _clamp01(float(self.global_multiplier))


def load_epistemic_config_from_env() -> EpistemicConfig:
    enabled = _env_flag("ROUTER_EPISTEMIC_ENABLED", "0")
    shadow_mode = _env_flag("ROUTER_EPISTEMIC_SHADOW_MODE", "1")
    pq_path = _safe_path(os.environ.get("ROUTER_EPISTEMIC_PQ_PATH"))
    pq_dir = _safe_path(os.environ.get("ROUTER_EPISTEMIC_PQ_DIR"))
    kernel_path = _safe_path(os.environ.get("ROUTER_EPISTEMIC_KERNEL_MULTIPLIERS_PATH"))
    use_kernel_multipliers = _env_flag("ROUTER_EPISTEMIC_USE_KERNEL_MULTIPLIERS", "0")

    try:
        max_staleness_s = int(os.environ.get("ROUTER_EPISTEMIC_MAX_STALENESS_S", "3600"))
    except Exception:
        max_staleness_s = 3600

    try:
        min_participation = float(os.environ.get("ROUTER_EPISTEMIC_MIN_PARTICIPATION", "0.0"))
    except Exception:
        min_participation = 0.0

    try:
        default_discount = float(os.environ.get("ROUTER_EPISTEMIC_DEFAULT_DISCOUNT", "1.0"))
    except Exception:
        default_discount = 1.0

    return EpistemicConfig(
        enabled=enabled,
        shadow_mode=shadow_mode,
        pq_path=pq_path,
        pq_dir=pq_dir,
        kernel_multipliers_path=kernel_path,
        max_staleness_s=max_staleness_s,
        min_participation=_clamp01(min_participation),
        default_discount=_clamp01(default_discount),
        use_kernel_multipliers=use_kernel_multipliers,
    )


def _parse_pq_entry(entry: Dict[str, Any]) -> Tuple[Optional[ParticipationQuality], Optional[str]]:
    try:
        score = _clamp01(float(entry.get("score", entry.get("participation_quality", 1.0))))
    except Exception:
        return None, "invalid_score"

    ts = entry.get("ts_utc", entry.get("timestamp"))
    ts_dt = _parse_ts(ts) if ts is not None else None

    state_distribution_raw = entry.get("state_distribution", {})
    state_distribution: Dict[str, float] = {}
    if isinstance(state_distribution_raw, dict):
        for key, value in state_distribution_raw.items():
            try:
                state_distribution[str(key).upper()] = max(0.0, float(value))
            except Exception:
                continue

    source = entry.get("source")
    return ParticipationQuality(
        score=score,
        ts_utc=ts_dt,
        state_distribution=state_distribution,
        source=str(source) if source is not None else None,
    ), None


def _extract_symbol_entry(payload: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    # Shape A: direct PQ object.
    if "score" in payload or "participation_quality" in payload:
        return payload

    # Shape B: by_symbol map.
    by_symbol = payload.get("by_symbol")
    if isinstance(by_symbol, dict):
        candidate = by_symbol.get(symbol) or by_symbol.get(symbol.upper())
        if isinstance(candidate, dict):
            return candidate

    # Shape C: symbol keyed root map.
    candidate = payload.get(symbol) or payload.get(symbol.upper())
    if isinstance(candidate, dict):
        return candidate

    return None


def load_participation_quality(
    symbol: str,
    config: EpistemicConfig,
) -> Tuple[Optional[ParticipationQuality], Optional[str]]:
    if not config.enabled:
        return None, "disabled"

    candidate_paths = []
    if config.pq_path is not None:
        candidate_paths.append(config.pq_path)
    if config.pq_dir is not None:
        candidate_paths.append(config.pq_dir / f"{symbol}.json")
        candidate_paths.append(config.pq_dir / f"{symbol.upper()}.json")

    if not candidate_paths:
        return None, "missing_pq_path"

    payload: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    for path in candidate_paths:
        if not path.exists():
            last_error = "pq_artifact_not_found"
            continue
        try:
            maybe_payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            last_error = f"pq_read_error:{type(exc).__name__}"
            continue
        if isinstance(maybe_payload, dict):
            payload = maybe_payload
            break
        last_error = "pq_invalid_payload"

    if payload is None:
        return None, last_error or "pq_unavailable"

    symbol_entry = _extract_symbol_entry(payload, symbol)
    if not isinstance(symbol_entry, dict):
        return None, "pq_symbol_missing"

    pq, parse_error = _parse_pq_entry(symbol_entry)
    if parse_error is not None or pq is None:
        return None, parse_error or "pq_parse_error"

    # If timestamp exists, enforce staleness bound.
    if pq.ts_utc is not None:
        age_s = (datetime.now(timezone.utc) - pq.ts_utc).total_seconds()
        if age_s > config.max_staleness_s:
            return None, "pq_stale"

    return pq, None


def load_kernel_multipliers(
    config: EpistemicConfig,
) -> Tuple[Optional[KernelMultipliers], Optional[str]]:
    if not config.enabled:
        return None, "disabled"
    if not config.use_kernel_multipliers:
        return None, "kernel_multipliers_disabled"
    if config.kernel_multipliers_path is None:
        return None, "missing_kernel_multipliers_path"
    if not config.kernel_multipliers_path.exists():
        return None, "kernel_multipliers_not_found"

    try:
        payload = json.loads(config.kernel_multipliers_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"kernel_multipliers_read_error:{type(exc).__name__}"

    if not isinstance(payload, dict):
        return None, "kernel_multipliers_invalid_payload"

    by_regime_raw = payload.get("by_regime", {})
    by_state_raw = payload.get("by_state", {})
    by_regime_state_raw = payload.get("by_regime_state", {})

    by_regime: Dict[str, float] = {}
    by_state: Dict[str, float] = {}
    by_regime_state: Dict[str, Dict[str, float]] = {}

    if isinstance(by_regime_raw, dict):
        for key, value in by_regime_raw.items():
            try:
                by_regime[str(key).lower()] = _clamp01(float(value))
            except Exception:
                continue

    if isinstance(by_state_raw, dict):
        for key, value in by_state_raw.items():
            try:
                by_state[str(key).upper()] = _clamp01(float(value))
            except Exception:
                continue

    if isinstance(by_regime_state_raw, dict):
        for regime_key, state_map in by_regime_state_raw.items():
            if not isinstance(state_map, dict):
                continue
            clean_state_map: Dict[str, float] = {}
            for state_key, value in state_map.items():
                try:
                    clean_state_map[str(state_key).upper()] = _clamp01(float(value))
                except Exception:
                    continue
            by_regime_state[str(regime_key).lower()] = clean_state_map

    try:
        global_multiplier = _clamp01(float(payload.get("global_multiplier", 1.0)))
    except Exception:
        global_multiplier = 1.0

    return KernelMultipliers(
        global_multiplier=global_multiplier,
        by_regime=by_regime,
        by_state=by_state,
        by_regime_state=by_regime_state,
    ), None


def compute_effective_size(
    base_size_q: int,
    pq: Optional[ParticipationQuality],
    config: EpistemicConfig,
    now: datetime,
    kernel_multipliers: Optional[KernelMultipliers] = None,
    regime: Optional[str] = None,
    epistemic_state: Optional[str] = None,
) -> Tuple[int, str, Dict[str, Any]]:
    """
    Compute effective quantized size under epistemic controls.

    Returns:
      (effective_size_q, rationale, metadata)
    """
    base_size_q = max(0, int(base_size_q))
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)

    metadata: Dict[str, Any] = {
        "enabled": config.enabled,
        "shadow_mode": config.shadow_mode,
        "base_size_q": base_size_q,
        "regime": regime,
        "state": epistemic_state,
        "ts_utc": now_utc.isoformat().replace("+00:00", "Z"),
    }

    if not config.enabled or base_size_q == 0:
        metadata["discount_applied"] = 1.0
        return base_size_q, "epistemic:disabled_or_zero", metadata

    discount = _clamp01(config.default_discount)
    detail_tokens = []

    if pq is None:
        detail_tokens.append("pq_missing")
        metadata["pq_missing"] = True
        if config.shadow_mode:
            metadata["discount_applied"] = 1.0
            metadata["would_apply_discount"] = discount
            metadata["would_be_size_q"] = int(base_size_q * discount + 0.5)
            return base_size_q, "epistemic:pq_missing(shadow)", metadata
    else:
        pq_discount = _clamp01(float(pq.score))
        discount = min(discount, pq_discount)
        metadata["pq_score"] = pq_discount
        if pq.ts_utc is not None:
            metadata["pq_ts_utc"] = pq.ts_utc.isoformat().replace("+00:00", "Z")
            metadata["pq_age_s"] = (now_utc - pq.ts_utc).total_seconds()
        if pq.state_distribution:
            metadata["state_distribution"] = pq.state_distribution
        detail_tokens.append(f"pq={pq_discount:.2f}")

        if pq_discount < config.min_participation:
            if config.shadow_mode:
                metadata["discount_applied"] = 1.0
                metadata["would_abstain"] = True
                return base_size_q, "epistemic:below_min_participation(shadow)", metadata
            metadata["discount_applied"] = 0.0
            return 0, "epistemic:below_min_participation", metadata

    if config.use_kernel_multipliers:
        if kernel_multipliers is None:
            metadata["kernel_multiplier_missing"] = True
            detail_tokens.append("kernel_missing")
        else:
            km = _clamp01(kernel_multipliers.lookup(regime=regime, state=epistemic_state))
            metadata["kernel_multiplier"] = km
            discount = min(discount, km)
            detail_tokens.append(f"kernel={km:.2f}")

    discount = _clamp01(discount)
    effective_size_q = int(base_size_q * discount + 0.5)
    metadata["discount_applied"] = discount
    metadata["would_be_size_q"] = effective_size_q

    if config.shadow_mode:
        token_suffix = ",".join(detail_tokens) if detail_tokens else "no_detail"
        return base_size_q, f"epistemic:shadow({token_suffix},d={discount:.2f})", metadata

    token_suffix = ",".join(detail_tokens) if detail_tokens else "no_detail"
    return effective_size_q, f"epistemic:applied({token_suffix},d={discount:.2f})", metadata
