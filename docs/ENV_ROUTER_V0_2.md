# Router v0.2 Environment Surface

This document lists runtime environment variables that affect sizing/adjunct behavior.

## Portfolio Pass

- `ROUTER_PORTFOLIO_ENABLE`
  - Default: `0` (disabled)
  - Values: `1/true/yes/on` enable; `0/false/no/off` disable
  - Effect: Enables portfolio-level correlation penalty and 100% exposure cap adjustment.
- `ROUTER_PORTFOLIO_ANCHOR_SYMBOL`
  - Default: unset
  - Effect: Optional anchor override for correlation penalty logic when enabled.

## Cross-Asset Throttle

- `ROUTER_CROSS_ASSET_ENABLED`
  - Default: `0` (disabled)
- `ROUTER_CROSS_ASSET_SHADOW_MODE`
  - Default: `1` (shadow/log-only)
- `ROUTER_CROSS_ASSET_THROTTLE_PATH`
  - Default: unset
  - Effect: Path to throttle JSON artifact.
- `ROUTER_CROSS_ASSET_MAX_STALENESS_S`
  - Default: `3600`
- `ROUTER_CROSS_ASSET_DEFAULT_THROTTLE`
  - Default: `1.0`

Fail-closed semantics when enabled:
- `shadow_mode=1`: artifact failures degrade to no-op throttle (`1.0`) with rationale.
- `shadow_mode=0`: artifact failures degrade to global risk-off (`0.0`).

## Epistemic Sizing

- `ROUTER_EPISTEMIC_ENABLED`
  - Default: `0` (disabled)
- `ROUTER_EPISTEMIC_SHADOW_MODE`
  - Default: `1` (shadow/log-only)
- `ROUTER_EPISTEMIC_PQ_PATH`
  - Default: unset
- `ROUTER_EPISTEMIC_PQ_DIR`
  - Default: unset
- `ROUTER_EPISTEMIC_KERNEL_MULTIPLIERS_PATH`
  - Default: unset
- `ROUTER_EPISTEMIC_MAX_STALENESS_S`
  - Default: `3600`
- `ROUTER_EPISTEMIC_MIN_PARTICIPATION`
  - Default: `0.0`
- `ROUTER_EPISTEMIC_DEFAULT_DISCOUNT`
  - Default: `1.0`
- `ROUTER_EPISTEMIC_USE_KERNEL_MULTIPLIERS`
  - Default: `0`

## Horizon Sizing

- `ROUTER_HORIZON_ENABLED`
  - Default: `0` (disabled)
- `ROUTER_HORIZON_SHADOW_MODE`
  - Default: `1` (shadow/log-only)
- `ROUTER_HORIZON_INDEX_PATH`
  - Default: unset
- `ROUTER_HORIZON_DEFAULT_MIN`
  - Default: `15`
- `ROUTER_HORIZON_DEFAULT_MULTIPLIER`
  - Default: `1.0`

## Defaults Summary

- Cross-asset adjunct: disabled by default.
- Epistemic adjunct: disabled by default.
- Horizon adjunct: disabled by default.
- Portfolio pass: disabled by default (explicit host enable required).
