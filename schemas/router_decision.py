"""
router.decision.v1 Schema

DOCTRINE: VETO_TIMESCALE

Schema hash: computed at module load
Frozen: 2026-01-23 (v1.0), Amended: 2026-01-23 (v1.1)

INVARIANTS:
- hold_required=True => system veto, never regime/micro conservatism
- hold_required=True => all blocks have plane=None
- hold_required=True => single block (system veto is atomic)
- No field removals; only additive changes with version bumps
- BlockReason.plane=None => system-level veto (not surface-originated)

SEMANTIC NOTES:
- "required": True means field must be present; value may be None where noted
- intended_hold_min: required=True, nullable=True (None when hold_required=True)
- hold_used: required=True, nullable=True (None when hold_required=True)

AMENDMENT 1.1 (2026-01-23):
- Added decision_authority field for observability decoupling
- Values: "shadow", "weak", "real"
- Default: "real" (backward compatible with 1.0 payloads)
- Purpose: Break observability deadlock (emit decisions at v0.1 authority)
- No impact on allow/block semantics; purely audit metadata
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

ROUTER_DECISION_SCHEMA_VERSION = "1.1"

# Accepted versions (1.0 payloads remain valid; missing decision_authority = "real")
ROUTER_DECISION_ACCEPTED_VERSIONS = {"1.0", "1.1"}

# Canonical schema definition for router.decision.v1
# Field ordering is deterministic for hash computation
ROUTER_DECISION_SCHEMA: Dict[str, Any] = {
    "version": "1.1",
    "frozen_date": "2026-01-23",
    "amended_date": "2026-01-23",
    "doctrine": "VETO_TIMESCALE",
    "fields": {
        # Core decision fields
        "asset": {
            "type": "str",
            "required": True,
            "nullable": False,
            "description": "Trading pair symbol",
        },
        "intended_hold_min": {
            "type": "int",
            "required": True,
            "nullable": True,  # None when hold_required=True
            "description": "Proposed holding period in minutes",
        },
        "policy_id": {
            "type": "str",
            "required": True,
            "nullable": False,
            "description": "Policy applied (P_HOLD_BLOCK_REGIME, etc.)",
        },
        "policy_defaulted": {
            "type": "bool",
            "required": True,
            "nullable": False,
            "description": "True if policy was not explicitly specified",
        },
        "outcome": {
            "type": "str",
            "required": True,
            "nullable": False,
            "values": ["ALLOW", "BLOCK"],
            "description": "Decision outcome",
        },
        "allowed": {
            "type": "bool",
            "required": True,
            "nullable": False,
            "description": "True if outcome == ALLOW",
        },

        # Hold tracking fields
        "hold_used": {
            "type": "int",
            "required": True,
            "nullable": True,  # None when hold_required=True
            "description": "Hold value used for evaluation",
        },
        "hold_required": {
            "type": "bool",
            "required": True,
            "nullable": False,
            "description": "True = system veto (input missing), never regime/micro",
        },

        # Block reasons
        "blocks": {
            "type": "list",
            "required": True,
            "nullable": False,
            "item_schema": {
                "plane": {
                    "type": "str",
                    "nullable": True,  # None for system veto
                    "values": ["regime", "micro", None],
                    "description": "Blocking plane (None = system veto)",
                },
                "code": {
                    "type": "str",
                    "nullable": False,
                    "description": "Block code (e.g., MISSING_HOLD, HOLD_GE_TURN_ON_15)",
                },
                "policy_id": {
                    "type": "str",
                    "nullable": False,
                    "description": "Policy that triggered block",
                },
                "threshold_min": {
                    "type": "int",
                    "nullable": True,
                    "description": "Threshold that was exceeded (None for system veto)",
                },
                "intended_hold_min": {
                    "type": "int",
                    "nullable": True,
                    "description": "Hold value that triggered block (None if missing)",
                },
                "surface_event_hash": {
                    "type": "str",
                    "nullable": True,
                    "description": "Hash of surface event (None for system veto)",
                },
            },
            "description": "List of block reasons",
        },

        # Annotations
        "annotations": {
            "type": "list",
            "required": True,
            "nullable": False,
            "item_type": "str",
            "description": "Informational annotations (not blocking)",
        },

        # Attached surfaces (audit trail)
        "attached_surfaces": {
            "type": "dict",
            "required": True,
            "nullable": False,
            "keys": ["regime", "micro"],
            "value_schema": {
                "present": {
                    "type": "bool",
                    "required": True,
                    "description": "True if surface was available",
                },
                "event_hash": {
                    "type": "str",
                    "nullable": True,
                    "description": "Surface event hash (if present)",
                },
                "status": {
                    "type": "str",
                    "nullable": True,
                    "description": "Surface status (if present)",
                },
                "turn_on_min": {
                    "type": "int",
                    "nullable": True,
                    "description": "Regime threshold (regime only)",
                },
                "unsafe_up_to_min": {
                    "type": "int",
                    "nullable": True,
                    "description": "Micro threshold (micro only)",
                },
            },
            "description": "Surface presence audit trail",
        },

        # Event metadata (added by make_decision_event_payload)
        "source_ts": {
            "type": "str",
            "required": True,
            "nullable": False,
            "description": "Source event timestamp (ISO 8601)",
        },
        "source_event_id": {
            "type": "str",
            "required": True,
            "nullable": False,
            "description": "Source event ID for provenance",
        },
        "schema_version": {
            "type": "str",
            "required": True,
            "nullable": False,
            "values": ["1.0", "1.1"],
            "description": "Schema version",
        },

        # Amendment 1.1: Authority observability
        "decision_authority": {
            "type": "str",
            "required": False,  # Optional for 1.0 backward compatibility
            "nullable": False,
            "values": ["shadow", "weak", "real"],
            "default": "real",
            "description": "Authority level at decision time (shadow/weak/real)",
            "added_version": "1.1",
        },
    },

    # Invariants (enforced by validator)
    "invariants": [
        {
            "name": "outcome_allowed_consistency",
            "rule": "outcome == 'ALLOW' iff allowed == True",
        },
        {
            "name": "hold_required_implies_system_veto",
            "rule": "hold_required == True => all blocks have plane == None",
        },
        {
            "name": "hold_required_implies_single_block",
            "rule": "hold_required == True => len(blocks) == 1",
        },
        {
            "name": "hold_required_implies_hold_used_none",
            "rule": "hold_required == True => hold_used == None",
        },
    ],
}


def compute_schema_hash() -> str:
    """
    Compute deterministic hash of the schema.

    Uses canonical JSON with sorted keys for reproducibility.
    """
    canonical = json.dumps(ROUTER_DECISION_SCHEMA, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# Frozen hash - computed once, verified at runtime
# This value was computed on 2026-01-23 and must not change
ROUTER_DECISION_SCHEMA_HASH = compute_schema_hash()


def validate_decision_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Validate a router.decision.v1 payload against the frozen schema.

    Args:
        payload: The decision payload to validate

    Returns:
        List of validation errors (empty if valid)

    Note:
        This validator is defensive but not exhaustive. It focuses on
        invariant preservation rather than complete type checking.
    """
    errors: List[str] = []

    # Version check (accept 1.0 and 1.1)
    schema_version = payload.get("schema_version")
    if schema_version not in ROUTER_DECISION_ACCEPTED_VERSIONS:
        errors.append(
            f"schema_version must be one of {ROUTER_DECISION_ACCEPTED_VERSIONS}, "
            f"got {schema_version}"
        )

    # decision_authority validation (1.1+, optional for 1.0)
    decision_authority = payload.get("decision_authority")
    if decision_authority is not None:
        valid_authorities = {"shadow", "weak", "real"}
        if decision_authority not in valid_authorities:
            errors.append(
                f"decision_authority must be one of {valid_authorities}, "
                f"got {decision_authority}"
            )

    # Required fields check (top-level)
    required_fields = [
        "asset", "intended_hold_min", "policy_id", "policy_defaulted",
        "outcome", "allowed", "hold_used", "hold_required",
        "blocks", "annotations", "attached_surfaces",
        "source_ts", "source_event_id", "schema_version",
    ]
    for field in required_fields:
        if field not in payload:
            errors.append(f"Missing required field: {field}")

    # Early return if missing fields
    if errors:
        return errors

    # Invariant 1: outcome_allowed_consistency
    outcome = payload.get("outcome")
    allowed = payload.get("allowed")
    if outcome == "ALLOW" and allowed is not True:
        errors.append("outcome='ALLOW' must have allowed=True")
    if outcome == "BLOCK" and allowed is not False:
        errors.append("outcome='BLOCK' must have allowed=False")

    # Invariant 2: hold_required implies system veto (plane=None)
    hold_required = payload.get("hold_required")
    blocks = payload.get("blocks", [])

    if hold_required is True:
        for i, block in enumerate(blocks):
            plane = block.get("plane")
            if plane is not None:
                errors.append(
                    f"hold_required=True requires plane=None for all blocks, "
                    f"but block[{i}] has plane={plane}"
                )

    # Invariant 3: hold_required implies single block
    if hold_required is True and len(blocks) != 1:
        errors.append(
            f"hold_required=True requires exactly 1 block, got {len(blocks)}"
        )

    # Invariant 4: hold_required implies hold_used=None
    if hold_required is True and payload.get("hold_used") is not None:
        errors.append(
            f"hold_required=True requires hold_used=None, "
            f"got hold_used={payload.get('hold_used')}"
        )

    # Validate attached_surfaces structure
    attached = payload.get("attached_surfaces", {})
    for plane in ["regime", "micro"]:
        if plane not in attached:
            errors.append(f"attached_surfaces missing '{plane}' key")
        elif "present" not in attached.get(plane, {}):
            errors.append(f"attached_surfaces.{plane} missing 'present' field")

    return errors


def validate_decision_payload_strict(payload: Dict[str, Any]) -> None:
    """
    Strict validation that raises on any error.

    Args:
        payload: The decision payload to validate

    Raises:
        ValueError: If any validation errors are found
    """
    errors = validate_decision_payload(payload)
    if errors:
        raise ValueError(
            f"router.decision.v1 validation failed: {'; '.join(errors)}"
        )


def is_system_veto(payload: Dict[str, Any]) -> bool:
    """
    Check if a decision is a system veto (not market-based).

    System vetoes represent infrastructure failures (missing input),
    not market-based decisions.

    Used by EVT scripts to exclude from regime stats and calibration.

    Args:
        payload: The decision payload to check

    Returns:
        True if this is a system veto, False otherwise
    """
    return (
        payload.get("hold_required") is True and
        payload.get("hold_used") is None
    )
