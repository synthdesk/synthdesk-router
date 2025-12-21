## veto matrix

the router must veto downstream propagation if any of the following hold:

| condition                             | veto |
|--------------------------------------|------|
| missing required modality            | yes  |
| contradictory classifications        | yes  |
| confidence below declared minimum    | yes  |
| semantic violation detected          | yes  |
| stale or invalid timestamp           | yes  |
| aggregate state indeterminate        | yes  |
| listener.downtime unresolved         | yes  |

veto is binary.
no overrides are permitted.
