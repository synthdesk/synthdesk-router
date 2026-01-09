# synthdesk-router v0.1

**Status:** Frozen runtime specification
**Freeze date:** 2026-01-02
**Authority:** Constitutional restraint layer

---

## Purpose

The router is a **deterministic intent synthesizer** that:

1. Consumes facts from the event spine
2. Applies hard-veto constraints
3. Synthesizes posture intent (not execution orders)
4. Emits `router.intent` or `router.veto` events

The router is **not smart**. It is **authoritative**.

---

## What the Router Does NOT Do

- ❌ No market observation (prices, indicators, signals)
- ❌ No strategy logic (optimization, alpha, edge)
- ❌ No execution (trades, orders, position management)
- ❌ No learning (feedback, adaptation, reinforcement)
- ❌ No cross-symbol strategy
- ❌ No API calls (OpenAI, exchanges, external data)

**If it does more than intent synthesis, it's wrong.**

---

## Runtime Architecture

### Process Shape

```
router/
├── main.py              # Long-running loop (single entrypoint)
├── spine_reader.py      # Reads append-only event spine
├── state.py             # Router-local state (regimes, violations, last_intent, last_veto_reason)
├── constraints.py       # Veto logic (pure functions)
├── intent.py            # Intent synthesis (pure, deterministic)
└── emit.py              # Writes router.intent/router.veto events to spine
```

Single entrypoint. Boring. Inspectable.

---

## Event Contracts

### Allowed Inputs (from spine only)

The router consumes exactly these event types:

1. `listener.start` → system alive signal
2. `listener.crash` → system failure, input unavailable → veto
3. `invariant.violation` → hard veto → veto
4. `market.regime` → regime classification update
5. `market.regime_change` → regime transition

**Nothing else.**

- No CSVs
- No tick logs
- No direct listener imports
- No file reads outside spine

### Output Events (to spine only)

The router emits two event types:

**`router.intent`**

Event schema:
```json
{
  "event_type": "router.intent",
  "payload": {
    "symbol": "BTC-USD",
    "direction": "long | short",
    "size_pct": 0.25,
    "risk_cap": "low | normal | high",
    "rationale": [
      "regime=drift"
    ]
  },
  "source_event_id": "evt_...",
  "source_ts": "2026-01-02T10:00:01Z"
}
```

**`router.veto`**

Event schema:
```json
{
  "event_type": "router.veto",
  "payload": {
    "symbol": "BTC-USD",
    "veto_reason": "invariant_violation | input_unavailable | regime_unresolved"
  },
  "source_event_id": "evt_...",
  "source_ts": "2026-01-02T10:00:01Z"
}
```

`regime_unresolved` means the router cannot derive exposure from the current regime state.

**Emission rules:**
- Intent: emit only on **change** (deduplicate by symbol); rationale explains exposure
- Veto: emit only on **reason change** (deduplicate by symbol); no rationale
- Preserve triggering provenance via `source_event_id` and `source_ts`

---

## State Model

Router keeps minimal memory to decide posture:

```python
state = {
  "symbols": {
    "BTC-USD": {
      "regime": "chop",              # from market.regime
      "last_regime_ts": "...",       # timestamp
      "last_intent": {...},          # last emitted intent (dedup)
      "last_veto_reason": "...",     # last emitted veto reason (dedup)
    },
    ...
  },
  "system": {
    "listener_alive": True,          # from listener.start/crash
    "last_listener_event_ts": "...",
    "violation_active": False,       # from invariant.violation
    "last_violation_ts": None,
  }
}
```

**State properties:**
- Derived **only** from events
- Reconstructible via replay
- Never written back upstream
- Ephemeral (process-scoped, no persistence)

---

## Constraint Layer (Veto Logic)

Constraints are **hard gates**, not opinions.

### Veto Conditions (VETO_MATRIX.md)

| Condition | Veto reason |
|-----------|-------------|
| `invariant.violation` seen | `invariant_violation` |
| `listener.crash` recent | `input_unavailable` |
| Regime unresolved or no-exposure regime | `regime_unresolved` |
| Stale or invalid timestamp | `input_unavailable` |
| Missing required modality | `input_unavailable` |

**Veto is binary. No overrides permitted.**

### Constraint Function (Pure)

```python
def evaluate_constraints(state: dict, symbol: str) -> dict | VetoReason:
    """Returns intent or veto reason. Pure function."""

    # Hard veto: invariant violation active
    if state["system"]["violation_active"]:
        return VetoReason.INVARIANT_VIOLATION

    # Hard veto: listener down / missing inputs
    if not state["system"]["listener_alive"]:
        return VetoReason.INPUT_UNAVAILABLE

    # Hard veto: regime unknown
    regime = state["symbols"].get(symbol, {}).get("regime")
    if regime is None:
        return VetoReason.REGIME_UNRESOLVED

    # Attempt intent synthesis
    intent = intent_for_regime(regime)
    if intent is None:
        return VetoReason.REGIME_UNRESOLVED

    return intent
```

---

## Intent Synthesis (Deterministic Mapping)

Intent answers one question:

> "Given the facts I've been told, what posture is permitted?"

### Frozen Regime → Intent Mapping (v0.1)

```python
INTENT_REGIMES = {
    "drift": {
        "direction": "long",
        "size_pct": 0.25,
        "risk_cap": "normal",
        "rationale": ["regime=drift"],
    },
    "breakout": {
        "direction": "long",
        "size_pct": 0.25,
        "risk_cap": "high",
        "rationale": ["regime=breakout"],
    },
}

def intent_for_regime(regime: str) -> dict | None:
    """Deterministic regime → intent mapping. Frozen."""
    return INTENT_REGIMES.get(regime)  # None => veto (high_vol, chop, unknown)
```

**This mapping is intentionally dumb.**

It is a declarative posture, not an order.

---

## Determinism Guarantees

### Replay Semantics

Given identical input spine → produces byte-identical output spine.

**Guarantees:**
1. No randomness
2. No external API calls
3. No time-of-day dependencies
4. No filesystem reads (except spine)
5. No network IO

### Reconstruction Property

Router state at time T is fully reconstructible by:
1. Reading spine from start
2. Replaying all events up to T
3. Applying deterministic state transitions

**No hidden state. No ambient context.**

---

## What This Immediately Unlocks

Once `router.intent` exists:

- ✅ Agency can reason about intent without guessing
- ✅ Tools can listen without coupling
- ✅ Execution can be added later without refactors
- ✅ Dashboards become meaningful
- ✅ Replay becomes institutional-grade

**Most importantly:** Authority is now explicit. No phantom decision-making.

---

## What v0.1 Does NOT Include (Yet)

The following are **downstream of intent** and explicitly deferred:

- Execution logic
- Auto-trading
- Learning from outcomes
- Signal weighting
- Cross-symbol strategy
- Raw price consumption
- LLM calls for intent synthesis

**These may come later. Not now.**

---

## Governance Foundation

Router authority is grounded in human-authored belief:

- **CONSTITUTION.md** - Foundational law (11 articles)
- **VETO_MATRIX.md** - Hard veto conditions
- **EXPECTATIONS.md** - Active regime beliefs (provisional, revisable)
- **STRATA.md** - Deep priors (historical regimes)
- **VIOLATIONS.md** - Append-only audit log

**Belief revision is human-mediated, not learned.**

---

## Acceptance Criteria (Golden Corpus)

### Test 1: Invariant Violation Emits Veto
**Input:**
```jsonl
{"event_type": "market.regime", "payload": {"symbol": "BTC-USD", "regime": "drift"}, ...}
{"event_type": "invariant.violation", ...}
```

**Expected output:**
```jsonl
{"event_type": "router.intent", "payload": {"symbol": "BTC-USD", "direction": "long", "size_pct": 0.25, ...}, "source_event_id": "...", "source_ts": "..."}
{"event_type": "router.veto", "payload": {"symbol": "BTC-USD", "veto_reason": "invariant_violation"}, "source_event_id": "...", "source_ts": "..."}
```

### Test 2: Listener Crash Emits Veto
**Input:**
```jsonl
{"event_type": "listener.start", ...}
{"event_type": "market.regime", "payload": {"symbol": "BTC-USD", "regime": "breakout"}, ...}
{"event_type": "listener.crash", ...}
```

**Expected output:**
```jsonl
{"event_type": "router.intent", "payload": {"symbol": "BTC-USD", "direction": "long", "size_pct": 0.25, ...}, "source_event_id": "...", "source_ts": "..."}
{"event_type": "router.veto", "payload": {"symbol": "BTC-USD", "veto_reason": "input_unavailable"}, "source_event_id": "...", "source_ts": "..."}
```

### Test 3: Regime Change Preserves Intent
**Input:**
```jsonl
{"event_type": "market.regime", "payload": {"symbol": "BTC-USD", "regime": "drift"}, ...}
{"event_type": "market.regime_change", "payload": {"symbol": "BTC-USD", "from": "drift", "to": "drift"}, ...}
```

**Expected output:**
```jsonl
{"event_type": "router.intent", "payload": {"symbol": "BTC-USD", "direction": "long", ...}, "source_event_id": "...", "source_ts": "..."}
```
(No second emission - intent unchanged)

### Test 4: Deduplication Works (Veto)
**Input:**
```jsonl
{"event_type": "market.regime", "payload": {"symbol": "BTC-USD", "regime": "chop"}, ...}
{"event_type": "market.regime", "payload": {"symbol": "BTC-USD", "regime": "chop"}, ...}
```

**Expected output:**
```jsonl
{"event_type": "router.veto", "payload": {"symbol": "BTC-USD", "veto_reason": "regime_unresolved"}, "source_event_id": "...", "source_ts": "..."}
```
(Only one emission)

---

## Archive

Historical artifacts are preserved under `archive/` and are non-runnable, non-authoritative.

---

## Installation

Router v0.1 requires:
- `synthdesk_spine` SDK (frozen schema)
- Python 3.11+
- Event spine at known path (configurable)

```bash
pip install -e /Users/lucas/dev/synthdesk/packages/spine_sdk
pip install -e /Users/lucas/dev/synthdesk/packages/router
```

---

## Running the Router

```bash
# Long-running daemon
python -m router.main

# Replay mode (determinism testing)
python -m router.main --replay event_spine.jsonl router_output.jsonl
```

---

## Philosophy

> "The router is not a brain. It is a constitution."

The router's success is measured not by what it enables, but by **what it prevents from happening too early**.

---

## Version History

- **v0.0** (2025-12-20) - Initial runtime birth (`router.permission` only)
- **v0.1** (2026-01-02) - Intent synthesis runtime (frozen spec)

---

## Status

**Router v0.1 is now formally lockable.**

Golden corpus gates enforce determinism.
Belief layer grounds authority.
State model is explicit and reconstructible.
Event contracts are frozen.

The foundation is locked.
