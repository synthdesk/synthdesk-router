# Amendment: router.decision.v1 Schema 1.1 - decision_authority

**Date**: 2026-01-23
**Doctrine**: VETO_TIMESCALE
**Type**: Additive Amendment (backward compatible)

## Problem Statement

Observability deadlock: The router.decision.v1 event was only emitted on the "real intent" emission path, which requires v0.2+ authority. This created a structural problem:

- Law gates decisions (decisions require authority)
- Decisions needed to verify law (can't observe without authority)
- Result: Cannot test decision logic at v0.1

## Proposed Change

Add `decision_authority` field to router.decision.v1 schema:

```
decision_authority: "shadow" | "weak" | "real"
```

- **shadow**: Decision evaluated for shadow intent (authority-gated counterfactual)
- **weak**: Decision evaluated for weak intent (question, not decision)
- **real**: Decision evaluated for real intent (execution authority granted)

## Schema Changes

- Version: 1.0 → 1.1
- New field: `decision_authority` (optional for 1.0 backward compatibility)
- Hash: `dad3e1d2824262be` → `c4bdc5fe80e2d991`

## Safety Argument

1. **Additive only**: No existing fields modified or removed
2. **Backward compatible**: 1.0 payloads (without decision_authority) remain valid
3. **Default behavior**: Missing decision_authority interpreted as "real"
4. **No impact on allow/block semantics**: Field is purely audit metadata
5. **Validator updated**: Rejects invalid authority values

## Implementation

1. `schemas/router_decision.py`: Added field, bumped version, updated validator
2. `router/surface_integration.py`: Added decision_authority param to make_decision_event_payload
3. `router/emit.py`: Added decision_authority param to emit_decision
4. `router/main.py`: Wired surface gate + decision emission on weak/shadow paths

## Test Evidence

21 golden tests pass:
- 16 existing invariant tests (unchanged)
- 5 new decision_authority tests:
  - test_decision_authority_default_is_real
  - test_decision_authority_shadow
  - test_decision_authority_weak
  - test_decision_authority_invalid_rejected
  - test_schema_version_1_0_accepted

## Verification Commands

```bash
# Local schema hash
python3 -c "from schemas.router_decision import ROUTER_DECISION_SCHEMA_HASH; print(ROUTER_DECISION_SCHEMA_HASH)"
# Expected: c4bdc5fe80e2d991

# Run golden tests
python3 -m pytest tests/test_surface_decision_golden.py -v
# Expected: 21 passed

# VPS schema hash (after deploy)
ssh root@157.180.79.228 'PYTHONPATH=/root/synthdesk/packages/router python3 -c "from schemas.router_decision import ROUTER_DECISION_SCHEMA_HASH; print(ROUTER_DECISION_SCHEMA_HASH)"'
# Expected: c4bdc5fe80e2d991
```

## Rollback

If issues arise:
1. Revert `schema_version` to "1.0" in emit.py
2. Remove decision_authority from emit_decision calls
3. Redeploy

1.0 payloads remain valid; no data loss.

## Verdict

**APPROVED** - Amendment is additive, backward compatible, and solves the observability deadlock without granting execution authority.
