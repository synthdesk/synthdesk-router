# Market Expectations

epistemic_contract:
- expectations are explicit, non-arbitrary beliefs derived from repeated observation
- they are not updated on a daily basis and do not track day-to-day commentary
- each expectation is a decisive but provisional belief, meant to be stressed,
  contradicted, or eventually retired by reality
- daily agency outputs may reference expectations but must not rewrite them

last_reviewed: 2025-12-18
scope: crypto spot / perp microstructure
confidence_scale: low | medium | high

---

## expectation: high-volatility with weak breakout acceptance

id: exp-2025-12-high-vol-weak-breakout
introduced: 2025-12-17
status: active
confidence: medium
origin_days: [2025-12-17, 2025-12-18]

observed pattern:
- frequent volatility spikes across the day
- breakout attempts often fail within the first reaction window
- mean reversion is common after short extensions
- btc and eth largely move in lockstep

expected behavior:
- volatility resolves more often via reversion than continuation
- early directional follow-through is unreliable
- continuation, when it occurs, requires delayed acceptance
  rather than immediate expansion

router posture:
- default: veto
- allow discretion only if:
  - an initial snap-back attempt fails
  - acceptance persists beyond the initial reaction window

last_confirmed: 2025-12-18
last_violated: null

notes:
- this expectation is grounded in consecutive daily observations
- repeated violations without reversion would require revision or retirement
