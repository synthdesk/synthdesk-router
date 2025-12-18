# Strata

STRATA is the long-memory belief layer for router authority.

epistemic_contract:
- strata are deep priors and historical regimes, grounded in multi-month or multi-year observation
- strata are rarely edited; changes should be deliberate and explicitly justified
- strata are not executable logic, not triggers, not thresholds, and not a model
- a router enforcement layer may read STRATA to justify which constraint categories exist at all
- the enforcement layer must never infer, learn, or revise strata

scope: crypto spot / perp microstructure
confidence_scale: low | medium | high

---

## strata record template

name: <short regime name>
introduced: <YYYY-MM-DD>
status: active | archived
confidence: low | medium | high
time_horizon: months | years

core priors:
- <descriptive prior>

historical support:
- <dates / episodes / references>

router authority justification:
- which veto families this strata legitimizes (categorical only)

notes:
- <non-operational context>

