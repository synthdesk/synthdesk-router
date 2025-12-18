# Violations (Append-Only)

This file is an append-only audit surface: a record of when reality contradicts
EXPECTATIONS (and, more rarely, STRATA).

epistemic_contract:
- violations do not auto-revise beliefs
- violations do not trigger updates
- violations are logged for human review and future belief revision requests
- an enforcement layer must never edit beliefs; it may only reference logged violations for audit linkage

append_only_rules:
- add new entries at the bottom
- do not rewrite prior entries (corrections must be appended as a new record)
- include evidence pointers (run ids, file paths, timestamps) where possible

---

## violation record template

date: <YYYY-MM-DD>
scope: crypto spot / perp microstructure

violates:
- expectation: <stable expectation id (preferred); fallback: expectation header text>
- strata: <optional>

severity: low | medium | high
confidence: low | medium | high

what happened (descriptive):
- <observable contradiction of expectation>

evidence pointers (audit):
- <runs/... path(s)>
- <agency report path(s)>
- <listener event/log path(s)>

notes:
- <non-operational context>
