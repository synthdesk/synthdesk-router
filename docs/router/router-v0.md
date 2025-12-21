---

router version: v0
role: permission gate
authority level: zero posture, zero execution

purpose:
the router v0 determines whether the system is permitted to act at all,
based solely on observation correctness facts.

inputs (consumed):
- listener.start
- listener.stop
- listener.crash
- listener.downtime
- invariant.violation (critical only)

explicit non-inputs:
- prices
- signals
- pnl
- strategies
- confidence scores

state:
- permission: allow | forbid

initial state:
- forbid

transition rules:
- on listener.start AND no critical invariant violations observed → allow
- on any critical invariant.violation → forbid
- on listener.crash → forbid
- on listener.stop → forbid
- on listener.downtime (unresolved) → forbid

resolution rule:
- a listener.downtime is resolved only by a subsequent listener.start with a later timestamp

output (emitted):
- router.permission

semantics:
- allow means "action may be considered downstream"
- forbid means "no action is allowed downstream"

non-goals:
- deciding direction
- deciding size
- placing trades
- retrying systems
- interpreting markets

---
