"""
test_event_authority.py - Lock in event-type constant usage for router.

Ensures router emitters use imported constants from spine_sdk,
not inline string literals. This test is a permanent guard
against regression to pre-gov/event-authority-v0 state.

Tranche: router (constitutional)
"""

import inspect

from synthdesk_spine.event_types import (
    ROUTER_VETO,
    ROUTER_INTENT,
    ROUTER_INTENT_WEAK,
    ROUTER_INTENT_SHADOW,
    ROUTER_HEARTBEAT,
    ROUTER_HEALTH_SUMMARY,
    ROUTER_PORTFOLIO_V0,
    ROUTER_AUTHORITY_DEMOTION,
    ROUTER_SPEECH_V1,
    COHERENCE_STATE,
    PERCEPTION_PRICE_LIVENESS,
    INVARIANT_VIOLATION,
    RISK_VETO_SURFACE_REGIME_V1,
    RISK_VETO_SURFACE_MICRO_V1,
)


class TestRouterConstantsMatchStrings:
    """Verify spine_sdk constants match expected event-type strings."""

    def test_router_veto(self):
        assert ROUTER_VETO == "router.veto"

    def test_router_intent(self):
        assert ROUTER_INTENT == "router.intent"

    def test_router_intent_weak(self):
        assert ROUTER_INTENT_WEAK == "router.intent_weak"

    def test_router_intent_shadow(self):
        assert ROUTER_INTENT_SHADOW == "router.intent_shadow"

    def test_router_heartbeat(self):
        assert ROUTER_HEARTBEAT == "router.heartbeat"

    def test_router_health_summary(self):
        assert ROUTER_HEALTH_SUMMARY == "router.health_summary"

    def test_router_portfolio_v0(self):
        assert ROUTER_PORTFOLIO_V0 == "router.portfolio.v0"

    def test_router_authority_demotion(self):
        assert ROUTER_AUTHORITY_DEMOTION == "router.authority_demotion"

    def test_router_speech_v1(self):
        assert ROUTER_SPEECH_V1 == "router.speech.v1"

    def test_coherence_state(self):
        assert COHERENCE_STATE == "coherence.state"

    def test_perception_price_liveness(self):
        assert PERCEPTION_PRICE_LIVENESS == "perception.price_liveness"

    def test_invariant_violation(self):
        assert INVARIANT_VIOLATION == "invariant.violation"

    def test_risk_veto_surface_regime_v1(self):
        assert RISK_VETO_SURFACE_REGIME_V1 == "risk.veto_surface.regime.v1"

    def test_risk_veto_surface_micro_v1(self):
        assert RISK_VETO_SURFACE_MICRO_V1 == "risk.veto_surface.micro.v1"


class TestNoStringLiteralsInEmitPy:
    """Verify no event-type string literals in emit.py."""

    def test_emit_imports_from_spine_sdk(self):
        """Verify emit.py imports constants from spine_sdk."""
        from router import emit
        source = inspect.getsource(emit)
        assert 'from synthdesk_spine.event_types import' in source

    def test_no_router_string_literals(self):
        """Scan emit.py for forbidden string literals."""
        from router import emit
        source = inspect.getsource(emit)

        forbidden = [
            '"router.veto"',
            '"router.intent"',
            '"router.intent_weak"',
            '"router.intent_shadow"',
            '"router.heartbeat"',
            '"router.health_summary"',
            "'router.veto'",
            "'router.intent'",
            "'router.intent_weak'",
            "'router.intent_shadow'",
            "'router.heartbeat'",
            "'router.health_summary'",
        ]

        for literal in forbidden:
            assert literal not in source, \
                f"Found forbidden string literal {literal} in emit.py"


class TestNoStringLiteralsInMainPy:
    """Verify no event-type string literals in main.py."""

    def test_main_imports_from_spine_sdk(self):
        """Verify main.py imports constants from spine_sdk."""
        from router import main
        source = inspect.getsource(main)
        assert 'from synthdesk_spine.event_types import' in source

    def test_no_router_string_literals(self):
        """Scan main.py for forbidden string literals."""
        from router import main
        source = inspect.getsource(main)

        forbidden = [
            '"router.authority_demotion"',
            '"perception.price_liveness"',
            "'router.authority_demotion'",
            "'perception.price_liveness'",
        ]

        for literal in forbidden:
            assert literal not in source, \
                f"Found forbidden string literal {literal} in main.py"
