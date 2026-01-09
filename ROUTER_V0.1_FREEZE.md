# Router v0.1 Freeze Complete

**Date:** 2026-01-02
**Status:** ✅ Formally lockable
**Determinism:** ✅ Golden gates passing

This document is authoritative.
Changes require constitutional amendment.

---

## What Was Built

Router v0.1 is a **deterministic intent synthesizer** that unifies runtime and replay logic into a single constitutional runtime.

### Zero Semantic Expansion

- **No new authority** - Router remains a restraint layer
- **No execution** - Emits posture intent, not orders
- **No learning** - Frozen regime → intent mapping
- **No API calls** - Spine consumption only

### Architectural Changes

Unified `router_v1.py` (permission gate) + `router_v1_replay.py` (intent synthesis) into:

```
router/
├── main.py           # Single entrypoint (runtime + replay modes)
├── spine_reader.py   # Event spine tail iterator
├── state.py          # Minimal, explicit state (reconstructible)
├── constraints.py    # Veto logic (pure functions)
├── intent.py         # Intent synthesis (frozen mapping)
└── emit.py           # router.intent emission
```

---

## What Changed

### New Runtime Structure

**Before:**
- `router_v1.py` - Permission gate only (`router.permission: allow`)
- `router_v1_replay.py` - Intent synthesis (offline only)
- Split authority, inconsistent event types

**After:**
- `router/main.py` - Unified runtime
- Consumes: `listener.start`, `listener.crash`, `invariant.violation`, `market.regime`, `market.regime_change`
- Emits: `router.intent` (payload + `source_event_id`/`source_ts`)
- Works in both daemon and replay modes

### Event Contract Change

**Old output:**
```
router.permission: allow  (stdout only, one-shot)
```

**New output:**
```jsonl
{"event_type": "router.intent", "payload": {"symbol": "BTC-USD", "direction": "long", "size_pct": 0.25, "risk_cap": "normal", "rationale": ["regime=drift"]}, "source_event_id": "...", "source_ts": "..."}
```

---

## Frozen Semantics

### Regime → Intent Mapping (Constitutional)

| Regime | Direction | Size % | Risk Cap |
|--------|-----------|--------|----------|
| `high_vol` | flat | 0.0 | low |
| `chop` | flat | 0.0 | low |
| `drift` | long | 0.25 | normal |
| `breakout` | long | 0.25 | high |
| unknown | flat | 0.0 | low |

**Changes to this mapping require constitutional amendment.**

### Hard Veto Conditions (VETO_MATRIX.md)

1. `invariant.violation` active → force flat
2. `listener.crash` detected → force flat
3. Regime unresolved → force flat

**Veto is binary. No overrides.**

---

## Golden Corpus Gates

### Test Coverage

✅ **01: Invariant Violation Forces Flat**
- Regime = drift → long intent
- Invariant violation → overrides to flat

✅ **02: Listener Crash Forces Flat**
- Regime = breakout → long intent
- Listener crash → overrides to flat

✅ **03: Regime Change No Duplicate**
- Regime = drift → long intent
- Regime change (drift → drift) → no new emission (dedup)

✅ **04: Deduplication Identical Regime**
- Regime = chop → flat intent
- Same regime again → no new emission (dedup)

### Determinism Enforcement

```bash
./tests/golden/check_golden.sh      # All 4 cases pass
./tests/golden/check_determinism.sh # Byte-identical across runs
```

**Status:** Both gates passing ✅

---

## State Model

Router keeps minimal ephemeral state:

```python
{
  "symbols": {
    "BTC-USD": {
      "regime": "drift",
      "last_regime_ts": "...",
      "last_intent": {...}  # for dedup
    }
  },
  "system": {
    "listener_alive": True,
    "violation_active": False,
    "last_listener_event_ts": "...",
    "last_violation_ts": None
  }
}
```

**Properties:**
- Derived only from events
- Reconstructible via replay
- Never persisted
- Never written back upstream

---

## Determinism Guarantees

Given identical input spine → produces byte-identical output:

1. ✅ No randomness
2. ✅ No external API calls
3. ✅ No time-of-day dependencies
4. ✅ No filesystem reads (except spine)
5. ✅ No network IO
6. ✅ Pure functions for all logic

**Router state at time T is fully reconstructible by replaying spine up to T.**

---

## Files Created

### Core Runtime
- `router/__init__.py` - Package init
- `router/main.py` - Unified runtime (155 lines)
- `router/spine_reader.py` - Event spine tailer (113 lines)
- `router/state.py` - State management (103 lines)
- `router/constraints.py` - Veto logic (79 lines)
- `router/intent.py` - Intent synthesis (58 lines)
- `router/emit.py` - Event emission (41 lines)

### Tests & Documentation
- `README.md` - Frozen v0.1 specification (436 lines)
- `tests/golden/cases/*.jsonl` - 4 golden test inputs
- `tests/golden/expected/*.jsonl` - 4 golden expected outputs
- `tests/golden/README.md` - Test documentation
- `tests/golden/check_golden.sh` - Golden corpus gate
- `tests/golden/check_determinism.sh` - Determinism gate

**Total:** 7 runtime files, 11 test/doc files

---

## Files Marked for Deletion

**Not deleted yet, awaiting user confirmation:**

1. `router_v0.py` - Retired runtime (already has RuntimeError)
2. `router_v1.py` - Superseded by `router/main.py`
3. `router_v1_replay.py` - Logic extracted into `router/`
4. `docs/router/router-v0.md` - Superseded spec

---

## What This Unlocks

With router v0.1 locked:

✅ **Agency can reason about intent** without guessing
✅ **Tools can listen** without coupling
✅ **Execution can be added later** without refactors
✅ **Dashboards become meaningful** (intent provenance)
✅ **Replay is institutional-grade** (deterministic)

**Most importantly:** Authority is now explicit. No phantom decision-making.

---

## Running the Router

### Long-running daemon
```bash
cd /Users/lucas/dev/synthdesk/packages/router
PYTHONPATH=. python3 -m router.main
```

### Replay mode (determinism testing)
```bash
PYTHONPATH=. python3 -m router.main --replay input_spine.jsonl output_intents.jsonl
```

### Run golden gates
```bash
./tests/golden/check_golden.sh      # Semantic freeze
./tests/golden/check_determinism.sh  # Determinism freeze
```

---

## Constitutional Status

Router v0.1 is now **constitutionally frozen**:

- ✅ Event contracts defined (README.md)
- ✅ State model explicit (state.py)
- ✅ Veto logic pure functions (constraints.py)
- ✅ Intent mapping frozen (intent.py)
- ✅ Golden corpus enforces semantics
- ✅ Determinism gates passing

**Schema upgrades require:**
1. Amendment to README.md (event contracts)
2. New golden test cases
3. Constitutional review (VETO_MATRIX.md)

---

## Philosophy

> "The router is not a brain. It is a constitution."

Router v0.1 success is measured not by what it enables, but by **what it prevents from happening too early**.

The foundation is locked.

---

## Next Steps

**Router work is complete.** The runtime is:
- Deterministic ✅
- Testable ✅
- Frozen ✅
- Lockable ✅

**Downstream opportunities** (not router concerns):
- Agency consumes `router.intent` events
- Execution layer (if/when needed) reads intent
- Dashboards visualize intent provenance
- Auditors replay for institutional compliance

**Router authority is now constitutionally grounded and deterministically verifiable.**
