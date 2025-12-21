---
router version: v1
role: permission gate (violation-binding)
authority level: zero posture, zero execution

purpose:
router v1 determines whether the system is permitted to act at all.
invariant violations are treated as binding facts.

canonical input:
- /root/synthdesk-listener/runs/0.2.0/event_spine.jsonl

consumed events:
- invariant.violation

explicit non-inputs:
- prices
- signals
- pnl
- strategies
- lifecycle events
- confidence metrics

state:
- blocked: boolean
- emitted: boolean

initial state:
- blocked = false
- emitted = false

startup rules:
- scan entire existing event spine from start to EOF
- if any invariant.violation exists → blocked = true

runtime rules:
- tail spine append-only
- on each new valid JSON line:
  - if event_type == "invariant.violation" → blocked = true (permanent)
  - if blocked == false AND emitted == false → emit allow once

output:
- stdout only
- at most one line:
  router.permission: allow

semantics:
- allow means "downstream action may be considered"
- silence means "permission not granted"
- violation permanently withholds permission for that process lifetime

non-goals:
- emitting forbid
- revocation
- retries
- timestamps
- execution
- writing files
- modifying the spine

correctness definition:
- silence under violation is correct behavior

---
