# Router v0.1 Golden Corpus

**Purpose:** Determinism enforcement for router output synthesis (intent + veto)

## Test Cases

### 01: Invariant Violation Emits Veto
**Input:** `cases/01_invariant_violation_forces_flat.jsonl`
**Expected:** `expected/01_invariant_violation_forces_flat.jsonl`

**Scenario:**
1. Listener starts
2. Market regime = drift → emits `router.intent` (long, 25%, normal)
3. Invariant violation occurs → emits `router.veto` (invariant_violation)

**Invariant:** Hard veto takes precedence over regime intent

---

### 02: Listener Crash Emits Veto
**Input:** `cases/02_listener_crash_forces_flat.jsonl`
**Expected:** `expected/02_listener_crash_forces_flat.jsonl`

**Scenario:**
1. Listener starts
2. Market regime = breakout → emits `router.intent` (long, 25%, high)
3. Listener crashes → emits `router.veto` (input_unavailable)

**Invariant:** System failure emits veto (input_unavailable)

---

### 03: Regime Change Preserves Intent (No Duplicate)
**Input:** `cases/03_regime_change_no_duplicate.jsonl`
**Expected:** `expected/03_regime_change_no_duplicate.jsonl`

**Scenario:**
1. Listener starts
2. Market regime = drift → emits `router.intent` (long, 25%, normal)
3. Regime change (drift → drift) → **no new emission** (intent unchanged)

**Invariant:** Deduplication prevents spam on identical intent

---

### 04: Deduplication Works for Identical Regime (Veto)
**Input:** `cases/04_deduplication_identical_regime.jsonl`
**Expected:** `expected/04_deduplication_identical_regime.jsonl`

**Scenario:**
1. Listener starts
2. Market regime = chop → emits `router.veto` (regime_unresolved)
3. Market regime = chop (again) → **no new emission** (veto reason unchanged)

**Invariant:** Identical regime classifications don't spam identical vetoes

---

## Running Golden Tests

```bash
# Run all golden tests
python -m pytest tests/golden/test_golden.py -v

# Run determinism check (replay twice, compare byte-for-byte)
./tests/golden/check_determinism.sh
```

## Acceptance Criteria

All tests must pass with **byte-identical** output:
- Same event order
- Same payloads
- Same rationale
- Same source event IDs

**No tolerance for non-determinism.**

---

## Adding New Tests

1. Create input spine: `cases/NN_test_name.jsonl`
2. Define expected output: `expected/NN_test_name.jsonl`
3. Document scenario and invariant in this README
4. Run tests, verify determinism

---

## Philosophy

> "Golden corpus freezes semantics. If output changes, it's either a bug or a constitutional amendment."

These tests are the **determinism gate** for router v0.1.
