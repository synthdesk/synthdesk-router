## schemas

the router operates on categorical, descriptive records only.

input: annotation_record
- id (opaque identifier)
- modality (categorical)
- classification (categorical)
- confidence_bucket (low | medium | high)
- timestamp
- provenance metadata

output: epistemic_snapshot
- aggregate_state (coherent | incoherent | indeterminate)
- constraint_flags (unordered set)
- salience_tags (unordered, non-ranked)
- veto_applied (true | false)
- audit_reference

note:
- audit_reference should be sufficient to reconstruct the exact input set and the belief basis in force (EXPECTATIONS.md / STRATA.md), without embedding action-legible semantics.

no ordered fields.
no numeric optimization surfaces.
no action-legible labels.
