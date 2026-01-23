"""
Regime Surface Serializer from EVT-1 Artifacts

DOCTRINE: VETO_TIMESCALE

Pure adapter: serializes from EVT-1 ladder artifacts to VetoSurfacePayload.
Does NOT compute from raw spectral/drawdown/tags.

Evidence chain: EVT-0, EVT-1A, EVT-1B (gate2_final_2026-01-20)
Added: 2026-01-23
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from synthdesk_spine.payloads.veto_surface import (
    HorizonRisk,
    VetoSurfaceInputs,
    VetoSurfacePayload,
    make_regime_surface,
)


# Frozen parameters from DOCTRINE_VETO_TIMESCALE
# Dominance threshold: ELV >= this value means abstention dominates
DOMINANCE_ELV_THRESHOLD_BPS = 5.0


def _get_code_sha() -> str:
    """Get current git SHA for code provenance."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def _horizon_to_risk(
    horizon_data: Dict[str, Any],
    elv_threshold_bps: float = DOMINANCE_ELV_THRESHOLD_BPS,
) -> HorizonRisk:
    """
    Convert EVT-1 horizon metrics to HorizonRisk.

    Dominance status:
    - DOMINANCE: ELV >= threshold (abstention wins)
    - NO_DOMINANCE: ELV < threshold (no clear advantage)
    """
    metrics = horizon_data.get("metrics", {}).get("overall", {})

    elv_bps = metrics.get("elv_bps")
    cvar_10_bps = metrics.get("cvar_10_bps")

    # Determine dominance status
    if elv_bps is None:
        status = "UNKNOWN"
    elif elv_bps >= elv_threshold_bps:
        status = "DOMINANCE"
    else:
        status = "NO_DOMINANCE"

    # Net bps = ELV - FAC (Expected Loss during Veto minus False Abstention Cost)
    # In EVT-1A, dominance_margin_bps = elv_bps (FAC already factored in)
    net_bps = metrics.get("dominance_margin_bps") or elv_bps

    # Confidence from coverage rate
    integrity = horizon_data.get("integrity", {})
    conf = integrity.get("coverage_rate")

    return HorizonRisk(
        status=status,
        net_bps=net_bps,
        cvar10_bps=cvar_10_bps,
        elv_bps=elv_bps,
        conf=conf,
    )


def _find_turn_on_horizon(
    horizons_data: Dict[str, Dict[str, Any]],
    elv_threshold_bps: float = DOMINANCE_ELV_THRESHOLD_BPS,
) -> Optional[int]:
    """
    Find the earliest horizon where dominance turns on.

    Returns the smallest horizon (in minutes) where ELV >= threshold.
    Returns None if no horizon shows dominance.
    """
    # Sort horizons by value
    horizon_values = sorted(int(h) for h in horizons_data.keys())

    for horizon in horizon_values:
        horizon_data = horizons_data[str(horizon)]
        metrics = horizon_data.get("metrics", {}).get("overall", {})
        elv_bps = metrics.get("elv_bps")

        if elv_bps is not None and elv_bps >= elv_threshold_bps:
            return horizon

    return None


def serialize_regime_surface(
    evt1_artifact_path: Union[str, Path],
    asset: str,
    *,
    elv_threshold_bps: float = DOMINANCE_ELV_THRESHOLD_BPS,
    custom_horizons: Optional[Tuple[int, ...]] = None,
) -> VetoSurfacePayload:
    """
    Serialize a regime surface from an EVT-1 ladder artifact.

    Pure adapter: reads EVT-1 results, does NOT recompute.

    Args:
        evt1_artifact_path: Path to EVT-1 results JSON (e.g., evt1a_results.json)
        asset: Asset symbol (e.g., "BTCUSDT"). Use "AGGREGATE" for combined data.
        elv_threshold_bps: ELV threshold for dominance determination (default: 5.0 bps)
        custom_horizons: Override horizon tuple (use EVT-1 horizons by default)

    Returns:
        VetoSurfacePayload for the regime plane

    Raises:
        FileNotFoundError: If artifact doesn't exist
        ValueError: If artifact format is invalid
        KeyError: If required fields missing from artifact
    """
    evt1_path = Path(evt1_artifact_path)
    if not evt1_path.exists():
        raise FileNotFoundError(f"EVT-1 artifact not found: {evt1_path}")

    # Load artifact
    with evt1_path.open("r", encoding="utf-8") as f:
        artifact_bytes = f.read()
        evt1_data = json.loads(artifact_bytes)

    # Validate artifact structure
    if "by_horizon" not in evt1_data:
        raise ValueError(f"Invalid EVT-1 artifact: missing 'by_horizon' field")

    horizons_data = evt1_data["by_horizon"]
    if not horizons_data:
        raise ValueError(f"Invalid EVT-1 artifact: 'by_horizon' is empty")

    # Extract horizons
    horizon_values = sorted(int(h) for h in horizons_data.keys())
    horizons_min = custom_horizons or tuple(horizon_values)

    # Build filtered horizons_data subset if custom_horizons provided
    # This ensures turn_on_min and risk_by_horizon operate on the same set
    if custom_horizons is not None:
        filtered_horizons_data = {
            h: horizons_data[h]
            for h in horizons_data
            if int(h) in custom_horizons
        }
    else:
        filtered_horizons_data = horizons_data

    # Build risk_by_horizon (operates on filtered set)
    risk_by_horizon: Dict[str, HorizonRisk] = {}
    for horizon_str, horizon_data in filtered_horizons_data.items():
        risk_by_horizon[horizon_str] = _horizon_to_risk(
            horizon_data, elv_threshold_bps
        )

    # Find turn_on_min (operates on same filtered set)
    turn_on_min = _find_turn_on_horizon(filtered_horizons_data, elv_threshold_bps)

    # Build reasons
    reasons_regime: List[str] = []

    if turn_on_min is not None:
        reasons_regime.append(f"DOMINANCE_ONSET_AT_{turn_on_min}M")

    # Add verdict from artifact
    combined_verdict = evt1_data.get("combined_verdict")
    if combined_verdict:
        reasons_regime.append(f"EVT1A_VERDICT:{combined_verdict}")

    # Note the evidence chain
    defendant_id = evt1_data.get("defendant_id", "unknown")
    reasons_regime.append(f"DEFENDANT:{defendant_id[:40]}")

    # Preserve threshold provenance for interpretability
    reasons_regime.append(f"ELV_THRESHOLD_BPS:{elv_threshold_bps}")

    # Build inputs
    artifact_hash = hashlib.sha256(artifact_bytes.encode("utf-8")).hexdigest()[:16]

    # Build params for reproducibility
    params = {
        "elv_threshold_bps": elv_threshold_bps,
        "asset": asset,
        "horizons": list(horizons_min),
    }
    params_hash = hashlib.sha256(
        json.dumps(params, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    inputs = VetoSurfaceInputs(
        evidence_refs=(str(evt1_path),),
        input_hash=artifact_hash,
        params_hash=params_hash,
        code_sha=_get_code_sha(),
    )

    # Determine status
    status = "ACTIVE" if risk_by_horizon else "NO_DATA"

    return make_regime_surface(
        asset=asset,
        horizons_min=horizons_min,
        turn_on_min=turn_on_min,
        risk_by_horizon=risk_by_horizon,
        reasons_regime=reasons_regime,
        inputs=inputs,
        status=status,
    )


def serialize_regime_surface_per_asset(
    evt1_artifact_path: Union[str, Path],
    *,
    assets: Optional[List[str]] = None,
    elv_threshold_bps: float = DOMINANCE_ELV_THRESHOLD_BPS,
) -> Dict[str, VetoSurfacePayload]:
    """
    Serialize regime surfaces for multiple assets from EVT-1 artifact.

    Args:
        evt1_artifact_path: Path to EVT-1 results JSON
        assets: List of assets to extract (default: all in artifact)
        elv_threshold_bps: ELV threshold for dominance determination

    Returns:
        Dict mapping asset -> VetoSurfacePayload
    """
    evt1_path = Path(evt1_artifact_path)

    # Read artifact bytes once for consistent hashing across all assets
    with evt1_path.open("r", encoding="utf-8") as f:
        artifact_bytes_str = f.read()
        evt1_data = json.loads(artifact_bytes_str)

    # Compute artifact hash once (same for all assets from this artifact)
    artifact_hash = hashlib.sha256(artifact_bytes_str.encode("utf-8")).hexdigest()[:16]

    horizons_data = evt1_data.get("by_horizon", {})
    if not horizons_data:
        return {}

    # Extract available assets from per-symbol data
    first_horizon = next(iter(horizons_data.values()), {})
    by_symbol = first_horizon.get("metrics", {}).get("by_symbol", {})
    available_assets = list(by_symbol.keys())

    if assets is None:
        assets = available_assets

    results = {}

    for asset in assets:
        if asset not in available_assets:
            continue

        # Build per-asset horizon data
        asset_horizons_data = {}
        for horizon_str, horizon_data in horizons_data.items():
            symbol_metrics = horizon_data.get("metrics", {}).get("by_symbol", {}).get(asset)
            if symbol_metrics:
                # Reconstruct horizon data structure for single asset
                asset_horizons_data[horizon_str] = {
                    "horizon_minutes": int(horizon_str),
                    "metrics": {
                        "overall": symbol_metrics,
                    },
                    "verdict": horizon_data.get("verdict"),
                    "integrity": {
                        # Use asset-specific count if available
                        "coverage_rate": horizon_data.get("integrity", {}).get("coverage_rate"),
                    },
                }

        if not asset_horizons_data:
            continue

        # Build asset-specific surface
        horizon_values = sorted(int(h) for h in asset_horizons_data.keys())
        horizons_min = tuple(horizon_values)

        risk_by_horizon: Dict[str, HorizonRisk] = {}
        for horizon_str, horizon_data in asset_horizons_data.items():
            risk_by_horizon[horizon_str] = _horizon_to_risk(
                horizon_data, elv_threshold_bps
            )

        turn_on_min = _find_turn_on_horizon(asset_horizons_data, elv_threshold_bps)

        reasons_regime = []
        if turn_on_min is not None:
            reasons_regime.append(f"DOMINANCE_ONSET_AT_{turn_on_min}M")
        reasons_regime.append(f"ASSET_SPECIFIC:{asset}")
        reasons_regime.append(f"ELV_THRESHOLD_BPS:{elv_threshold_bps}")

        # artifact_hash computed once above (consistent across all assets)

        params = {
            "elv_threshold_bps": elv_threshold_bps,
            "asset": asset,
            "horizons": list(horizons_min),
        }
        params_hash = hashlib.sha256(
            json.dumps(params, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        inputs = VetoSurfaceInputs(
            evidence_refs=(str(evt1_path),),
            input_hash=artifact_hash,
            params_hash=params_hash,
            code_sha=_get_code_sha(),
        )

        results[asset] = make_regime_surface(
            asset=asset,
            horizons_min=horizons_min,
            turn_on_min=turn_on_min,
            risk_by_horizon=risk_by_horizon,
            reasons_regime=reasons_regime,
            inputs=inputs,
            status="ACTIVE" if risk_by_horizon else "NO_DATA",
        )

    return results
