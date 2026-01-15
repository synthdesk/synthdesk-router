# Kernel Policy (Frozen)

**Effective:** 2026-01-15
**Status:** Constitutional

## Core Principle

Each horizon requires its own kernel, calibration, and EVT-0. No kernel may claim competence outside its validated horizon.

## Rules

1. **One kernel, one horizon** - A kernel is calibrated and validated for exactly one forward-looking horizon
2. **No blending** - The router selects kernels, never averages their outputs
3. **Independent falsification** - Each kernel must pass EVT-0 independently before production use
4. **Kill without cascade** - A failed kernel is removed; the system continues with remaining kernels

## Kernel Lifecycle

```
DESIGN → CALIBRATE → EVT-0 → PASS/FAIL
                              ↓
                         PASS: production
                         FAIL: archive or abandon
```

## Why This Matters

Markets have different generators at different scales:
- 5m: momentum/microstructure
- 15m-60m: mean reversion
- 1h+: regime/structural

Pretending one model works everywhere is how systems start lying politely.

---

*This policy exists because EVT-0 failed on 2026-01-15. The failure proved that horizon-specific calibration is mandatory, not optional.*
