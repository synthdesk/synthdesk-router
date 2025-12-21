---

event_type: router.permission

source: router
version: router/v0

payload:
- permission: allow | forbid
- reason: string
- since_event_id: uuid

meaning:
this event declares whether downstream action is permitted.

notes:
- permission does not imply obligation
- forbid is the safe default
- repeated identical permissions may be suppressed

---
