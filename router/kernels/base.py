"""
Kernel Interface - Frozen contract for all direction kernels.

All kernels must implement this interface. No exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class KernelEnvelope:
    """Output from any direction kernel."""

    # Probability distribution
    p_long: float
    p_short: float
    p_flat: float
    p_vetoed: float

    # Epistemic contract
    directional: bool

    # Horizon binding (frozen per kernel)
    horizon_minutes: int

    # Provenance (frozen per kernel build)
    kernel_name: str
    kernel_version: str
    kernel_build_sha: str

    # Input signals (for audit)
    z_mean: float
    z_std: float
    regime: str

    def to_dict(self) -> Dict:
        return {
            "p_long": self.p_long,
            "p_short": self.p_short,
            "p_flat": self.p_flat,
            "p_vetoed": self.p_vetoed,
            "directional": self.directional,
            "horizon_minutes": self.horizon_minutes,
            "kernel": self.kernel_name,
            "kernel_version": self.kernel_version,
            "kernel_build_sha": self.kernel_build_sha,
            "z_mean": self.z_mean,
            "z_std": self.z_std,
            "regime": self.regime,
        }


@runtime_checkable
class KernelInterface(Protocol):
    """
    All direction kernels implement this interface.

    Properties (class-level, frozen):
        horizon_minutes: The ONLY horizon this kernel is valid for
        kernel_name: Unique identifier
        kernel_version: Semver-ish version string
        kernel_build_sha: Hash of calibration + code
    """

    horizon_minutes: int
    kernel_name: str
    kernel_version: str
    kernel_build_sha: str

    def make_envelope(
        self,
        symbol: str,
        prices: List[float],
        z_mean: Optional[float],
        z_std: Optional[float],
        regime: Optional[str],
        source_event_id: str,
        p_vetoed: float = 0.0,
    ) -> KernelEnvelope:
        """
        Generate probability envelope for this kernel's horizon.

        Args:
            symbol: Asset identifier
            prices: Recent price history (for signal computation)
            z_mean: Normalized drift from regime classifier
            z_std: Normalized volatility
            regime: Current regime label
            source_event_id: For deterministic seeding
            p_vetoed: Veto probability from risk kernel

        Returns:
            KernelEnvelope with direction claim or non-directional
        """
        ...


__all__ = ["KernelEnvelope", "KernelInterface"]
