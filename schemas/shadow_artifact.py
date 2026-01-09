"""Shadow artifact schema for router.veto events.

This schema defines the contract for shadow router veto artifacts.
OMA consumes these artifacts but does not interpret them.

All fields required. No optionality. No derived fields.
"""

from enum import Enum
from typing import Literal, TypedDict


class VetoReason(str, Enum):
    """Frozen veto reasons. Matches router.constraints.VetoReason."""

    invariant_violation = "invariant_violation"
    input_unavailable = "input_unavailable"
    regime_unresolved = "regime_unresolved"


class ShadowVetoArtifact(TypedDict):
    """
    Shadow router veto artifact.

    Emitted by shadow router when authority is withheld.
    Read-only for OMA. No interpretation permitted.
    """

    # Contract identity
    shadow: Literal[True]

    # Causal provenance
    source_event_id: str  # id of the spine event that triggered evaluation
    source_ts: str  # ISO-8601 timestamp from the source spine event
    observed_at: str  # ISO-8601 timestamp when shadow_router observed/emitted

    # Router run provenance
    router_run_id: str  # stable identifier for the shadow router run

    # Veto semantics
    veto_reason: VetoReason


def validate_shadow_veto_artifact(artifact: dict) -> None:
    """
    Validate shadow veto artifact against schema.

    Raises ValueError if invalid.
    """
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be dict")

    # shadow must be True
    if artifact.get("shadow") is not True:
        raise ValueError("shadow must be True")

    # All string fields required and non-empty
    for field in ("source_event_id", "source_ts", "observed_at", "router_run_id"):
        value = artifact.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be non-empty string")

    # veto_reason must be valid enum value
    veto_reason = artifact.get("veto_reason")
    valid_reasons = {r.value for r in VetoReason}
    if veto_reason not in valid_reasons:
        raise ValueError(f"veto_reason must be one of {valid_reasons}")
