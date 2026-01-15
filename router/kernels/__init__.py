# Kernel registry
#
# Each kernel owns exactly one horizon. No blending.
# See KERNEL_POLICY.md for constitutional rules.

from .base import KernelEnvelope, KernelInterface

# Verified kernels (passed EVT-0 holdout)
from .momentum_5m import (
    Momentum5mKernel,
    Momentum5mParams,
    make_momentum_5m_envelope,
)

# Failed kernels (archived for reference - DO NOT USE)
from .reversion_15m import (
    Reversion15mKernel,
    Reversion15mParams,
    make_reversion_15m_envelope,
)  # FAILED: 41.3% hit rate on holdout

__all__ = [
    # Interface
    "KernelEnvelope",
    "KernelInterface",
    # 5m momentum (VERIFIED)
    "Momentum5mKernel",
    "Momentum5mParams",
    "make_momentum_5m_envelope",
    # 15m reversion (FAILED - archived)
    "Reversion15mKernel",
    "Reversion15mParams",
    "make_reversion_15m_envelope",
]
