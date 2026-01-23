"""
Golden Regression Tests for Surface Decision Logic.

CONSTITUTIONAL: These tests freeze exact behavior for system vetoes.
Any change to these outputs requires court process.

FROZEN: 2026-01-23
DOCTRINE: VETO_TIMESCALE

Invariants enforced:
- intended_hold_min=None => SURFACE_MISSING_HOLD veto
- hold_required=True => system veto (plane=None)
- Surface presence always recorded (even when vetoed)
- No policy defaulting or fallback for missing inputs
"""

import pytest

from router.constraints import VetoReason
from router.surface_integration import evaluate_surface_gate
from router.surface_policy import DecisionOutcome, PolicyId
from synthdesk_spine.payloads.veto_surface import (
    VetoSurfaceInputs,
    VetoSurfaceIntegrity,
    VetoSurfacePayload,
)


# -----------------------------------------------------------------------------
# Test Fixtures
# -----------------------------------------------------------------------------


def _make_test_inputs() -> VetoSurfaceInputs:
    """Create minimal VetoSurfaceInputs for testing."""
    return VetoSurfaceInputs(
        evidence_refs=("test_evidence",),
        input_hash="test_input_hash",
        params_hash="test_params_hash",
        code_sha="test_code_sha",
    )


def _make_test_integrity(plane: str) -> VetoSurfaceIntegrity:
    """Create VetoSurfaceIntegrity for testing."""
    return VetoSurfaceIntegrity(
        event_hash=f"test_{plane}_hash",
        prev_event_hash=None,
    )


def make_active_regime_surface(turn_on_min: int) -> VetoSurfacePayload:
    """
    Create a minimal ACTIVE regime surface for testing.

    Args:
        turn_on_min: Horizon where abstention dominates (block if hold >= this)
    """
    return VetoSurfacePayload(
        asset="BTCUSDT",
        plane="regime",
        schema_version="1.0",
        status="ACTIVE",
        horizons_min=(5, 15, 60, 240),
        turn_on_min=turn_on_min,
        unsafe_up_to_min=None,  # Must be None for regime
        risk_by_horizon={},
        reasons={"regime": ["test_regime_reason"], "micro": []},
        inputs=_make_test_inputs(),
        integrity=_make_test_integrity("regime"),
    )


def make_active_micro_surface(unsafe_up_to_min: int) -> VetoSurfacePayload:
    """
    Create a minimal ACTIVE micro surface for testing.

    Args:
        unsafe_up_to_min: Scalping unsafe up to this horizon (block if hold <= this)
    """
    return VetoSurfacePayload(
        asset="BTCUSDT",
        plane="micro",
        schema_version="1.0",
        status="ACTIVE",
        horizons_min=(5, 15, 60, 240),
        turn_on_min=None,  # Must be None for micro
        unsafe_up_to_min=unsafe_up_to_min,
        risk_by_horizon={},
        reasons={"regime": [], "micro": ["test_micro_reason"]},
        inputs=_make_test_inputs(),
        integrity=_make_test_integrity("micro"),
    )


# -----------------------------------------------------------------------------
# CONSTITUTIONAL: Golden Tests for SURFACE_MISSING_HOLD
# -----------------------------------------------------------------------------


class TestSurfaceMissingHoldGolden:
    """
    CONSTITUTIONAL: Missing hold is a SYSTEM veto, not regime/micro conservatism.

    These tests freeze the exact behavior and must never be weakened.
    Any change to this output requires court process.

    FROZEN: 2026-01-23

    Invariants:
    - intended_hold_min=None => DecisionOutcome.BLOCK
    - VetoReason = SURFACE_MISSING_HOLD
    - BlockReason.plane = None (system veto, NOT regime/micro)
    - hold_required = True
    - hold_used = None
    - attached_surfaces records presence (even for system veto)
    - policy_defaulted = False (no fallback)
    """

    def test_missing_hold_exact_output(self):
        """
        Golden test: intended_hold_min=None produces exactly this output.

        This is THE canonical test for the missing hold invariant.
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=None,  # <-- THE INPUT
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        # 1. Outcome: BLOCKED
        assert allowed is False, "Missing hold must block"
        assert decision.outcome == DecisionOutcome.BLOCK

        # 2. VetoReason: SURFACE_MISSING_HOLD
        assert veto_reason == VetoReason.SURFACE_MISSING_HOLD

        # 3. BlockReason: plane=None (system veto, not regime/micro)
        assert len(decision.blocks) == 1, "System veto must be single-block"
        block = decision.blocks[0]
        assert block.plane is None, "CRITICAL: system veto must have plane=None"
        assert block.code == "MISSING_HOLD"

        # 4. Hold fields
        assert decision.hold_required is True, "Missing hold must set hold_required=True"
        assert decision.hold_used is None, "Missing hold must have hold_used=None"

        # 5. Surface presence recorded (even when vetoed)
        assert "regime" in decision.attached_surfaces
        assert "micro" in decision.attached_surfaces
        assert decision.attached_surfaces["regime"]["present"] is True
        assert decision.attached_surfaces["micro"]["present"] is True
        # Verify surface details are captured
        assert decision.attached_surfaces["regime"]["event_hash"] == "test_regime_hash"
        assert decision.attached_surfaces["micro"]["event_hash"] == "test_micro_hash"

        # 6. No policy defaulting, no fallback
        assert decision.policy_defaulted is False
        assert decision.intended_hold_min is None  # Preserved as-is

    def test_missing_hold_with_no_surfaces(self):
        """
        Missing hold veto even when surfaces are None.

        System veto for missing input takes precedence over surface absence.
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=None,
            regime_surface=None,  # No regime surface
            micro_surface=None,  # No micro surface
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        # Core assertions
        assert allowed is False
        assert veto_reason == VetoReason.SURFACE_MISSING_HOLD
        assert decision.hold_required is True
        assert decision.hold_used is None

        # Single block with plane=None
        assert len(decision.blocks) == 1
        assert decision.blocks[0].plane is None
        assert decision.blocks[0].code == "MISSING_HOLD"

        # Surface presence: both recorded as absent
        assert decision.attached_surfaces["regime"]["present"] is False
        assert decision.attached_surfaces["micro"]["present"] is False

    def test_missing_hold_invariant_negative_invalid(self):
        """
        Negative hold also triggers SURFACE_MISSING_HOLD.

        Invalid input (negative) is treated as missing, not as a value.
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=-5,  # Invalid: negative
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert allowed is False
        assert veto_reason == VetoReason.SURFACE_MISSING_HOLD
        assert decision.hold_required is True
        assert decision.hold_used is None
        assert decision.blocks[0].plane is None

    def test_missing_hold_invariant_zero_invalid(self):
        """
        Zero hold also triggers SURFACE_MISSING_HOLD.

        Zero is not a valid hold duration; treated as missing input.
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=0,  # Invalid: zero
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert allowed is False
        assert veto_reason == VetoReason.SURFACE_MISSING_HOLD
        assert decision.hold_required is True
        assert decision.hold_used is None
        assert decision.blocks[0].plane is None

    def test_missing_hold_all_policies(self):
        """
        Missing hold veto applies regardless of policy.

        System veto is policy-independent.
        """
        for policy in PolicyId:
            allowed, veto_reason, decision = evaluate_surface_gate(
                asset="BTCUSDT",
                intended_hold_min=None,
                regime_surface=make_active_regime_surface(turn_on_min=15),
                micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
                policy_id=policy,
            )

            assert allowed is False, f"Policy {policy} must block on missing hold"
            assert veto_reason == VetoReason.SURFACE_MISSING_HOLD
            assert decision.hold_required is True
            assert decision.blocks[0].plane is None, f"Policy {policy} must have plane=None"


class TestSystemVetoInvariants:
    """
    Additional invariants for system veto behavior.

    These ensure the system veto semantics remain distinct from surface vetoes.
    """

    def test_system_veto_single_block(self):
        """
        System veto must produce exactly one BlockReason.

        Multi-block futures must not accidentally create system vetoes with
        multiple blocks.
        """
        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=None,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_UNION_SAFETY,  # Most aggressive policy
        )

        assert len(decision.blocks) == 1, "System veto must be single-block"

    def test_hold_required_implies_plane_none(self):
        """
        Whenever hold_required=True, all blocks must have plane=None.

        This is the constitutional invariant that distinguishes system vetoes
        from surface vetoes.
        """
        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=None,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        if decision.hold_required is True:
            for block in decision.blocks:
                assert block.plane is None, (
                    f"hold_required=True requires plane=None, got plane={block.plane}"
                )

    def test_valid_hold_does_not_set_hold_required(self):
        """
        Valid hold input must NOT set hold_required=True.

        hold_required=True is exclusively for missing/invalid input.
        """
        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=60,  # Valid hold
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        # This should be a regime block, not a system veto
        assert decision.hold_required is False
        assert decision.hold_used == 60

        # If blocked, it's by regime (not system)
        if decision.outcome == DecisionOutcome.BLOCK:
            assert any(b.plane == "regime" for b in decision.blocks)


class TestSurfaceVetoDistinction:
    """
    Tests that verify surface vetoes are distinct from system vetoes.

    These ensure we can distinguish between:
    - System veto: infrastructure failure (missing input)
    - Surface veto: market-based decision (regime/micro block)
    """

    def test_regime_block_has_plane_regime(self):
        """
        Regime surface block must have plane='regime'.

        This distinguishes it from system veto (plane=None).
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=60,  # >= turn_on_min (15)
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert allowed is False
        assert veto_reason == VetoReason.SURFACE_REGIME_BLOCK
        assert decision.hold_required is False  # NOT a system veto
        assert decision.blocks[0].plane == "regime"

    def test_micro_block_has_plane_micro(self):
        """
        Micro surface block must have plane='micro'.

        This distinguishes it from system veto (plane=None).
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=3,  # <= unsafe_up_to_min (5)
            regime_surface=None,
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_SCALP_BLOCK_MICRO,
        )

        assert allowed is False
        assert veto_reason == VetoReason.SURFACE_MICRO_BLOCK
        assert decision.hold_required is False  # NOT a system veto
        assert decision.blocks[0].plane == "micro"

    def test_allow_decision_preserves_hold_fields(self):
        """
        Allowed decisions must have correct hold field semantics.
        """
        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=10,  # Between unsafe_up_to (5) and turn_on (15)
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert allowed is True
        assert veto_reason is None
        assert decision.outcome == DecisionOutcome.ALLOW
        assert decision.hold_required is False
        assert decision.hold_used == 10
        assert len(decision.blocks) == 0


# -----------------------------------------------------------------------------
# CONSTITUTIONAL: Golden Tests for Valid Hold Propagation
# -----------------------------------------------------------------------------


class TestValidHoldPropagationGolden:
    """
    CONSTITUTIONAL: Valid hold must propagate correctly.

    Mirrors the missing hold tests. Freezes the presence case.

    This prevents future refactors from "forgetting" to propagate time.

    FROZEN: 2026-01-23

    Invariants:
    - hold_required=False when valid hold provided
    - hold_used equals input value
    - intended_hold_min preserved in decision
    - Blocks record intended_hold_min for audit
    - Surfaces evaluated (not short-circuited)
    """

    def test_valid_hold_exact_propagation(self):
        """
        Golden test: intended_hold_min=X propagates exactly.

        This prevents future refactors from "forgetting" to propagate time.
        """
        HOLD_VALUE = 60  # Arbitrary valid hold

        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=HOLD_VALUE,  # <-- THE INPUT
            regime_surface=make_active_regime_surface(turn_on_min=15),  # Will block
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        # 1. Hold fields propagated correctly
        assert decision.hold_required is False, "Valid hold must NOT set hold_required"
        assert decision.hold_used == HOLD_VALUE, f"hold_used must be {HOLD_VALUE}"
        assert decision.intended_hold_min == HOLD_VALUE, "intended_hold_min preserved"

        # 2. Surfaces were evaluated (not short-circuited)
        assert "regime" in decision.attached_surfaces
        assert "micro" in decision.attached_surfaces

        # 3. If blocked, it's by surface (not system)
        if decision.outcome == DecisionOutcome.BLOCK:
            for block in decision.blocks:
                assert block.plane is not None, (
                    "Valid hold block must have plane != None (surface veto)"
                )
                assert block.intended_hold_min == HOLD_VALUE, (
                    "Block must record the hold that triggered it"
                )

    def test_valid_hold_allow_case(self):
        """
        Golden test: valid hold in safe range allows with correct fields.
        """
        HOLD_VALUE = 10  # Between unsafe_up_to (5) and turn_on (15)

        allowed, veto_reason, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=HOLD_VALUE,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        # Must allow
        assert allowed is True
        assert veto_reason is None
        assert decision.outcome == DecisionOutcome.ALLOW

        # Hold fields
        assert decision.hold_required is False
        assert decision.hold_used == HOLD_VALUE
        assert decision.intended_hold_min == HOLD_VALUE

        # No blocks
        assert len(decision.blocks) == 0

    def test_valid_hold_block_records_hold_in_block(self):
        """
        When blocked by surface, the block must record intended_hold_min.

        This ensures the blocking threshold can be compared to the intended hold.
        """
        HOLD_VALUE = 60  # >= turn_on_min (15)

        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=HOLD_VALUE,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert decision.outcome == DecisionOutcome.BLOCK
        assert len(decision.blocks) == 1

        block = decision.blocks[0]
        assert block.plane == "regime"
        assert block.intended_hold_min == HOLD_VALUE
        assert block.threshold_min == 15  # The turn_on_min that triggered

    def test_valid_hold_minimum_boundary(self):
        """
        Minimum valid hold (1 minute) must propagate correctly.
        """
        HOLD_VALUE = 1  # Minimum valid hold

        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=HOLD_VALUE,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        assert decision.hold_required is False
        assert decision.hold_used == HOLD_VALUE
        assert decision.intended_hold_min == HOLD_VALUE

    def test_valid_hold_all_policies(self):
        """
        Valid hold propagation must work for all policies.
        """
        HOLD_VALUE = 30

        for policy in PolicyId:
            _, _, decision = evaluate_surface_gate(
                asset="BTCUSDT",
                intended_hold_min=HOLD_VALUE,
                regime_surface=make_active_regime_surface(turn_on_min=15),
                micro_surface=make_active_micro_surface(unsafe_up_to_min=5),
                policy_id=policy,
            )

            assert decision.hold_required is False, f"Policy {policy} must not set hold_required"
            assert decision.hold_used == HOLD_VALUE, f"Policy {policy} must propagate hold_used"
            assert decision.intended_hold_min == HOLD_VALUE, f"Policy {policy} must preserve intended_hold_min"


# -----------------------------------------------------------------------------
# AMENDMENT 1.1: Golden Tests for decision_authority
# -----------------------------------------------------------------------------


class TestDecisionAuthorityGolden:
    """
    CONSTITUTIONAL: decision_authority field observability.

    AMENDMENT 1.1 (2026-01-23)

    Freezes behavior:
    - decision_authority field validates ("shadow", "weak", "real")
    - Default is "real" for backward compatibility
    - Field is additive; does not affect allow/block semantics
    """

    def test_decision_authority_default_is_real(self):
        """
        make_decision_event_payload defaults decision_authority to 'real'.
        """
        from router.surface_integration import make_decision_event_payload

        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=10,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        payload = make_decision_event_payload(
            decision,
            source_ts="2026-01-23T00:00:00Z",
            source_event_id="test",
        )

        assert payload["decision_authority"] == "real"
        assert payload["schema_version"] == "1.1"

    def test_decision_authority_shadow(self):
        """
        decision_authority='shadow' validates and propagates.
        """
        from router.surface_integration import make_decision_event_payload

        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=10,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        payload = make_decision_event_payload(
            decision,
            source_ts="2026-01-23T00:00:00Z",
            source_event_id="test",
            decision_authority="shadow",
        )

        assert payload["decision_authority"] == "shadow"

    def test_decision_authority_weak(self):
        """
        decision_authority='weak' validates and propagates.
        """
        from router.surface_integration import make_decision_event_payload

        _, _, decision = evaluate_surface_gate(
            asset="BTCUSDT",
            intended_hold_min=10,
            regime_surface=make_active_regime_surface(turn_on_min=15),
            micro_surface=None,
            policy_id=PolicyId.P_HOLD_BLOCK_REGIME,
        )

        payload = make_decision_event_payload(
            decision,
            source_ts="2026-01-23T00:00:00Z",
            source_event_id="test",
            decision_authority="weak",
        )

        assert payload["decision_authority"] == "weak"

    def test_decision_authority_invalid_rejected(self):
        """
        Invalid decision_authority values are rejected by validator.
        """
        from schemas.router_decision import validate_decision_payload

        # Construct a minimal valid payload with invalid authority
        payload = {
            "asset": "BTCUSDT",
            "intended_hold_min": 10,
            "policy_id": "P_HOLD_BLOCK_REGIME",
            "policy_defaulted": False,
            "outcome": "ALLOW",
            "allowed": True,
            "hold_used": 10,
            "hold_required": False,
            "blocks": [],
            "annotations": [],
            "attached_surfaces": {
                "regime": {"present": True, "event_hash": "test"},
                "micro": {"present": False},
            },
            "source_ts": "2026-01-23T00:00:00Z",
            "source_event_id": "test",
            "schema_version": "1.1",
            "decision_authority": "invalid_authority",  # INVALID
        }

        errors = validate_decision_payload(payload)
        assert any("decision_authority" in e for e in errors), \
            "Invalid decision_authority must be rejected"

    def test_schema_version_1_0_accepted(self):
        """
        Schema version 1.0 payloads (without decision_authority) are accepted.

        Backward compatibility invariant.
        """
        from schemas.router_decision import validate_decision_payload

        # Construct a 1.0 payload (no decision_authority)
        payload = {
            "asset": "BTCUSDT",
            "intended_hold_min": 10,
            "policy_id": "P_HOLD_BLOCK_REGIME",
            "policy_defaulted": False,
            "outcome": "ALLOW",
            "allowed": True,
            "hold_used": 10,
            "hold_required": False,
            "blocks": [],
            "annotations": [],
            "attached_surfaces": {
                "regime": {"present": True, "event_hash": "test"},
                "micro": {"present": False},
            },
            "source_ts": "2026-01-23T00:00:00Z",
            "source_event_id": "test",
            "schema_version": "1.0",
            # NOTE: No decision_authority field (1.0 payload)
        }

        errors = validate_decision_payload(payload)
        assert len(errors) == 0, f"1.0 payload must be valid: {errors}"
