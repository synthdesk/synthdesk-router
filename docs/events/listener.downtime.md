---

event_type: listener.downtime

source: synthdesk_watchdog
version: listener/<VERSION> (from synthdesk.listener.version)

payload:
- reason: never_seen_alive | listener.stop_observed | listener.crash_observed | heartbeat_gap_exceeded | heartbeat_missing | heartbeat_file_missing
- gap_seconds: int | null
- threshold_seconds: int
- poll_interval_seconds: float
- last_seen_timestamp: iso-8601 | null
- last_heartbeat_timestamp: iso-8601 | null
- last_heartbeat_path: string | null
- last_listener_event_type: listener.start | listener.stop | listener.crash | null
- last_listener_event_timestamp: iso-8601 | null
- last_listener_event_id: uuid | null

emitted_when:
- watchdog observes no recent heartbeat or lifecycle event beyond the threshold window
- watchdog has never observed a listener heartbeat or lifecycle event (first-ever silence)

meaning:
this event declares the listener is absent relative to the watchdog observation window.

does_not_imply:
- no cause is inferred (no crash diagnosis)
- no remediation or restart is attempted
- no statement about downstream permission or action

downstream_interpretation:
- router should treat unresolved downtime as a veto condition
- ops may alert or annotate audits
- downstream systems must not infer cause or intent

notes:
- resolution requires a later listener.start with a timestamp after the downtime
- this event is descriptive only

---
