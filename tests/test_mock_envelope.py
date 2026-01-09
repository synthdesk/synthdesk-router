"""
Mock Envelope Determinism Tests.

Validates that the mock envelope adapter is:
1. Deterministic (same inputs -> same outputs)
2. Correctly collapses on veto
3. Produces valid probability distributions
"""

from router.envelope import Envelope, make_mock_envelope


def test_mock_envelope_deterministic():
    """Same inputs must produce identical outputs."""
    a = make_mock_envelope(intent_side="LONG", confidence=0.42, vetoed=False, size=0.10).to_dict()
    b = make_mock_envelope(intent_side="LONG", confidence=0.42, vetoed=False, size=0.10).to_dict()
    assert a == b


def test_mock_envelope_veto_forces_collapse():
    """Vetoed envelope must have p_vetoed=1.0 and zero sizing."""
    e = make_mock_envelope(intent_side="LONG", confidence=0.99, vetoed=True, size=1.0).to_dict()
    assert e["p_vetoed"] == 1.0
    assert e["p_flat"] == 0.0
    assert e["p_long"] == 0.0
    assert e["p_short"] == 0.0
    assert e["size_min"] == 0.0
    assert e["size_max"] == 0.0


def test_mock_envelope_long_direction():
    """LONG intent should have p_long > 0 and p_short = 0."""
    e = make_mock_envelope(intent_side="LONG", confidence=0.8, vetoed=False, size=0.25)
    assert e.p_long > 0.0
    assert e.p_short == 0.0
    assert e.p_vetoed == 0.0


def test_mock_envelope_short_direction():
    """SHORT intent should have p_short > 0 and p_long = 0."""
    e = make_mock_envelope(intent_side="SHORT", confidence=0.8, vetoed=False, size=0.25)
    assert e.p_short > 0.0
    assert e.p_long == 0.0
    assert e.p_vetoed == 0.0


def test_mock_envelope_flat_direction():
    """FLAT intent should have p_flat = 1.0."""
    e = make_mock_envelope(intent_side="FLAT", confidence=0.8, vetoed=False, size=0.0)
    assert e.p_flat == 1.0
    assert e.p_long == 0.0
    assert e.p_short == 0.0
    assert e.p_vetoed == 0.0


def test_mock_envelope_probabilities_sum_to_one():
    """Non-vetoed envelope direction probs should sum to ~1.0."""
    for side in ["LONG", "SHORT", "FLAT"]:
        for conf in [0.0, 0.5, 1.0]:
            e = make_mock_envelope(intent_side=side, confidence=conf, vetoed=False, size=0.1)
            total = e.p_flat + e.p_long + e.p_short + e.p_vetoed
            assert abs(total - 1.0) < 0.01, f"Probs don't sum to 1: {total} for {side}, conf={conf}"


def test_mock_envelope_sizing_band_widens_with_low_confidence():
    """Lower confidence should produce wider sizing bands."""
    e_high = make_mock_envelope(intent_side="LONG", confidence=0.9, vetoed=False, size=0.5)
    e_low = make_mock_envelope(intent_side="LONG", confidence=0.1, vetoed=False, size=0.5)

    band_high = e_high.size_max - e_high.size_min
    band_low = e_low.size_max - e_low.size_min

    assert band_low > band_high, "Low confidence should have wider band"


def test_mock_envelope_kernel_version():
    """Envelope should include kernel and version for provenance."""
    e = make_mock_envelope(intent_side="LONG", confidence=0.5, vetoed=False, size=0.1)
    assert e.kernel == "mock_v0"
    assert e.version == "0.0.1"


def test_mock_envelope_to_dict_roundtrip():
    """to_dict should produce a valid dict with all fields."""
    e = make_mock_envelope(intent_side="LONG", confidence=0.5, vetoed=False, size=0.2)
    d = e.to_dict()

    assert isinstance(d, dict)
    assert set(d.keys()) == {"p_flat", "p_long", "p_short", "p_vetoed", "size_min", "size_max", "kernel", "version"}
    assert all(isinstance(v, (float, str)) for v in d.values())
