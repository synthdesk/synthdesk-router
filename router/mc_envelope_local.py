"""
Local Deterministic Monte Carlo Envelope Adapter (Phase 3)

Pure-Python MC envelope generator using the same deterministic hash-based
RNG as quant_cortex/mc_envelope_deterministic.py, but without Modal/GPU/torch.

Contract:
- Byte-identical output given identical inputs (deterministic)
- Same seed derivation as Modal version
- Same aggregation logic
- Zero external dependencies (no Modal, no torch, no vault)

Usage:
    envelope = generate_mc_envelope(
        intent_side="LONG",
        confidence=0.7,
        vetoed=False,
        size=0.25,
        symbol="BTCUSDT",
        spine_slice_hash="abc123...",
    )
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from router.envelope import Envelope


# ---------------------------
# Constants (frozen, from mc_envelope_deterministic.py)
# ---------------------------

MC_KERNEL_VERSION = "mc_local_v0.2.1"  # v0.2.1: logit shrink for temporal decay
H = 16  # Reduced horizon for local (vs 64 on GPU)
K = 8   # Channels (same as GPU)
N_SIMS = 64  # Reduced further for faster local runs (was 256)

# Horizon grid: maps H indices to approximate minutes
# H=16 steps span [1, 61] minutes (avoid degenerate idx=0 "immediate" case)
# Grid: 1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49, 53, 57, 61
HORIZON_GRID_MINUTES = [1 + i * 4 for i in range(H)]  # [1, 5, 9, ..., 61]


def _horizon_index(horizon_minutes: int) -> int:
    """Map requested horizon in minutes to nearest H index."""
    # Find nearest index
    best_idx = 0
    best_dist = abs(HORIZON_GRID_MINUTES[0] - horizon_minutes)
    for i, grid_min in enumerate(HORIZON_GRID_MINUTES):
        dist = abs(grid_min - horizon_minutes)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx

# Sigma schedules (frozen v0.1)
SIGMA_DIR_0 = 0.05
SIGMA_DIR_1 = 0.35
SIGMA_VETO_0 = 0.05
SIGMA_VETO_1 = 0.25
EPSILON = 0.01  # smoothing for one-hot → logits

# Logit shrink schedule (v0.2.1): horizon-dependent decay toward uncertainty
# alpha(h) = exp(-SHRINK_K * t) where t = h/(H-1)
# Interpretation: predictive log-odds decay with horizon unless reinforced by data
# SHRINK_K calibrated so p_long decays from ~0.85 to ~0.70 at max horizon
SHRINK_K = 0.5  # exp(-0.5) ≈ 0.607 at t=1


# ---------------------------
# Deterministic RNG (identical to GPU version)
# ---------------------------

def _u64_from_sha256(s: str) -> int:
    """Convert string to deterministic u64."""
    h = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(h[:8], byteorder="little", signed=False)


def _hash_to_uniform(seed_b: int, s: int, h: int, j: int) -> float:
    """Deterministic uniform in (0,1) from indices."""
    u = _u64_from_sha256(f"{seed_b}|s={s}|h={h}|j={j}")
    return (u + 0.5) / (2**64)


def _hash_to_normal(seed_b: int, s: int, h: int, j: int) -> float:
    """Deterministic normal via Box-Muller."""
    r1 = _hash_to_uniform(seed_b, s, h, j)
    r2 = _hash_to_uniform(seed_b, s, h, j + 1000)
    # Box-Muller: z = sqrt(-2 ln r1) * cos(2π r2)
    z = math.sqrt(-2.0 * math.log(r1 + 1e-10)) * math.cos(2.0 * math.pi * r2)
    return z


def _logit(p: float) -> float:
    """Logit function."""
    p = max(1e-6, min(1.0 - 1e-6, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    """Sigmoid function."""
    if x > 20:
        return 1.0
    if x < -20:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _softmax(logits: list) -> list:
    """Softmax over list of logits."""
    max_logit = max(logits)
    exp_logits = [math.exp(l - max_logit) for l in logits]
    sum_exp = sum(exp_logits)
    return [e / sum_exp for e in exp_logits]


# ---------------------------
# MC Kernel (local, pure-Python)
# ---------------------------

@dataclass(frozen=True)
class MCEnvelopeResult:
    """Full MC envelope result with provenance."""

    # Reduced envelope (what router sees)
    envelope: Envelope

    # Provenance
    kernel_version: str
    seed_u64: int
    input_hash: str
    output_hash: str

    # Full tensor stats (for comparison)
    channel_means: Dict[str, float]
    channel_stds: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "kernel_version": self.kernel_version,
            "seed_u64": str(self.seed_u64),
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "channel_means": self.channel_means,
            "channel_stds": self.channel_stds,
        }


def _run_mc_simulations(
    dir_logits0: list,
    veto_logit0: float,
    seed_b: int,
    h_max: int = H - 1,
) -> Dict[str, list]:
    """
    Run MC simulations over horizons [0, h_max].

    Args:
        h_max: Maximum horizon index to simulate (0 to H-1).
               For efficiency, only simulate up to the needed horizon.

    Returns dict of channel values [h_max+1] for each channel.
    """
    channels = {
        "p_flat": [],
        "p_long": [],
        "p_short": [],
        "p_vetoed": [],
        "survival_prob": [],
        "uncertainty": [],
    }

    # Only simulate up to h_max (inclusive)
    for h in range(min(h_max + 1, H)):
        # Sigma schedules
        t = h / (H - 1) if H > 1 else 0.0
        sigma_dir_h = SIGMA_DIR_0 + (SIGMA_DIR_1 - SIGMA_DIR_0) * t
        sigma_veto_h = SIGMA_VETO_0 + (SIGMA_VETO_1 - SIGMA_VETO_0) * t

        # Logit shrink: alpha(h) = exp(-k * t)
        # As horizon grows, shrink logits toward 0 (uniform distribution)
        alpha_h = math.exp(-SHRINK_K * t)

        # Per-sim samples
        p_flat_samples = []
        p_long_samples = []
        p_short_samples = []
        p_veto_samples = []
        survive_samples = []
        entropy_samples = []

        for s in range(N_SIMS):
            # Direction: shrink + perturb logits
            z_dir = [
                _hash_to_normal(seed_b, s, h, 0),
                _hash_to_normal(seed_b, s, h, 1),
                _hash_to_normal(seed_b, s, h, 2),
            ]
            dir_logits = [
                alpha_h * dir_logits0[j] + sigma_dir_h * z_dir[j] for j in range(3)
            ]
            p_dir = _softmax(dir_logits)

            p_flat_samples.append(p_dir[0])
            p_long_samples.append(p_dir[1])
            p_short_samples.append(p_dir[2])

            # Veto: perturb logit
            z_veto = _hash_to_normal(seed_b, s, h, 100)
            veto_logit = veto_logit0 + sigma_veto_h * z_veto
            p_veto = _sigmoid(veto_logit)
            p_veto_samples.append(p_veto)

            # Survival: product over steps [0..h]
            survive = 1.0
            for i in range(h + 1):
                z_v = _hash_to_normal(seed_b, s, i, 100)
                t_i = i / (H - 1) if H > 1 else 0.0
                sigma_v_i = SIGMA_VETO_0 + (SIGMA_VETO_1 - SIGMA_VETO_0) * t_i
                vl = veto_logit0 + sigma_v_i * z_v
                pv = _sigmoid(vl)
                survive *= 1.0 - pv
            survive_samples.append(survive)

            # Uncertainty: entropy of direction distribution
            entropy = -sum(
                p * math.log(p + 1e-9) for p in p_dir if p > 0
            )
            max_entropy = math.log(3.0)
            entropy_norm = entropy / max_entropy
            entropy_samples.append(entropy_norm)

        # Aggregate
        channels["p_flat"].append(sum(p_flat_samples) / N_SIMS)
        channels["p_long"].append(sum(p_long_samples) / N_SIMS)
        channels["p_short"].append(sum(p_short_samples) / N_SIMS)
        channels["p_vetoed"].append(sum(p_veto_samples) / N_SIMS)
        channels["survival_prob"].append(sum(survive_samples) / N_SIMS)
        channels["uncertainty"].append(sum(entropy_samples) / N_SIMS)

    return channels


def _reduce_channels_to_envelope(
    channels: Dict[str, list],
    size: float,
    vetoed: bool,
    horizon_idx: int = 0,
) -> Envelope:
    """
    Reduce H-horizon channels to single Envelope.

    Args:
        channels: Per-horizon MC channel values
        size: Base position size
        vetoed: If True, returns veto envelope
        horizon_idx: Which horizon slice to use for direction probs (0 to H-1)

    Reduction rule: use specified horizon for direction probs,
    weighted average for sizing band.
    """
    if vetoed:
        return Envelope(
            p_flat=0.0,
            p_long=0.0,
            p_short=0.0,
            p_vetoed=1.0,
            size_min=0.0,
            size_max=0.0,
            kernel="mc_local_v0",
            version=MC_KERNEL_VERSION,
        )

    # Clamp horizon_idx to valid range
    h_idx = max(0, min(len(channels["p_flat"]) - 1, horizon_idx))

    # Direction probs: use specified horizon slice
    p_flat = channels["p_flat"][h_idx]
    p_long = channels["p_long"][h_idx]
    p_short = channels["p_short"][h_idx]
    p_vetoed = channels["p_vetoed"][h_idx]

    # Sizing band: use uncertainty to modulate
    # Higher uncertainty → wider band
    avg_uncertainty = sum(channels["uncertainty"]) / len(channels["uncertainty"])
    band = 0.20 + 0.60 * avg_uncertainty  # Same formula as mock

    s = abs(size)
    size_min = max(0.0, s * (1.0 - band))
    size_max = s * (1.0 + band)

    return Envelope(
        p_flat=max(0.0, min(1.0, p_flat)),
        p_long=max(0.0, min(1.0, p_long)),
        p_short=max(0.0, min(1.0, p_short)),
        p_vetoed=max(0.0, min(1.0, p_vetoed)),
        size_min=size_min,
        size_max=size_max,
        kernel="mc_local_v0",
        version=MC_KERNEL_VERSION,
    )


def _compute_channel_stats(channels: Dict[str, list]) -> tuple:
    """Compute mean and std for each channel."""
    means = {}
    stds = {}
    for name, values in channels.items():
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)
        means[name] = round(mean, 6)
        stds[name] = round(std, 6)
    return means, stds


def generate_mc_envelope(
    *,
    intent_side: str,
    confidence: float,
    vetoed: bool,
    size: float,
    symbol: str,
    spine_slice_hash: str,
    horizon_minutes: int = 5,
) -> MCEnvelopeResult:
    """
    Generate deterministic MC envelope.

    Args:
        intent_side: "LONG", "SHORT", or "FLAT"
        confidence: [0, 1] - base confidence from allocator
        vetoed: If True, forces p_vetoed=1.0
        size: Base size
        symbol: Symbol for seed derivation
        spine_slice_hash: Hash of spine slice for seed derivation
        horizon_minutes: Prediction horizon in minutes (selects H slice)

    Returns:
        MCEnvelopeResult with envelope and full provenance
    """
    # Input hash for provenance
    input_str = f"{intent_side}|{confidence}|{vetoed}|{size}|{symbol}|{spine_slice_hash}"
    input_hash = hashlib.sha256(input_str.encode()).hexdigest()[:16]

    # Seed derivation (same logic as GPU version)
    seed_b = _u64_from_sha256(
        f"mc_local_v0.1|{spine_slice_hash}|{symbol}|{MC_KERNEL_VERSION}"
    )

    if vetoed:
        # Short-circuit for veto
        envelope = Envelope(
            p_flat=0.0,
            p_long=0.0,
            p_short=0.0,
            p_vetoed=1.0,
            size_min=0.0,
            size_max=0.0,
            kernel="mc_local_v0",
            version=MC_KERNEL_VERSION,
        )
        return MCEnvelopeResult(
            envelope=envelope,
            kernel_version=MC_KERNEL_VERSION,
            seed_u64=seed_b,
            input_hash=input_hash,
            output_hash="vetoed",
            channel_means={},
            channel_stds={},
        )

    # Convert intent to initial logits
    c = max(0.0, min(1.0, confidence))

    # Base direction one-hot with smoothing
    side = (intent_side or "FLAT").upper()
    if side == "LONG":
        dir_onehot = [0.15, 0.85, 0.0]  # flat, long, short
    elif side == "SHORT":
        dir_onehot = [0.15, 0.0, 0.85]
    else:
        dir_onehot = [0.90, 0.05, 0.05]

    # Modulate by confidence
    # Higher confidence → sharper distribution
    if side in ("LONG", "SHORT"):
        sharpness = 0.5 + 0.5 * c  # c=0 → 0.5, c=1 → 1.0
        if side == "LONG":
            dir_onehot = [
                0.15 * (1 - sharpness) + 0.33 * (1 - sharpness),
                0.85 * sharpness + 0.34 * (1 - sharpness),
                0.0 * sharpness + 0.33 * (1 - sharpness),
            ]
        else:
            dir_onehot = [
                0.15 * (1 - sharpness) + 0.33 * (1 - sharpness),
                0.0 * sharpness + 0.33 * (1 - sharpness),
                0.85 * sharpness + 0.34 * (1 - sharpness),
            ]

    # Normalize and smooth
    total = sum(dir_onehot)
    dir_onehot = [p / total for p in dir_onehot]
    p_dir = [p * (1.0 - 2 * EPSILON) + EPSILON for p in dir_onehot]
    dir_logits0 = [math.log(p) for p in p_dir]

    # Base veto probability (low for intents)
    p_veto0 = 0.05 + 0.10 * (1 - c)  # Lower confidence → slightly higher veto
    veto_logit0 = _logit(p_veto0)

    # Map horizon_minutes to H index
    h_idx = _horizon_index(horizon_minutes)

    # Run MC only up to needed horizon (optimization)
    channels = _run_mc_simulations(dir_logits0, veto_logit0, seed_b, h_max=h_idx)

    # Reduce to envelope using specified horizon slice (now at index h_idx of truncated channels)
    envelope = _reduce_channels_to_envelope(channels, size, vetoed, horizon_idx=h_idx)

    # Compute stats
    means, stds = _compute_channel_stats(channels)

    # Output hash
    output_str = f"{envelope.p_flat}|{envelope.p_long}|{envelope.p_short}|{envelope.p_vetoed}"
    output_hash = hashlib.sha256(output_str.encode()).hexdigest()[:16]

    return MCEnvelopeResult(
        envelope=envelope,
        kernel_version=MC_KERNEL_VERSION,
        seed_u64=seed_b,
        input_hash=input_hash,
        output_hash=output_hash,
        channel_means=means,
        channel_stds=stds,
    )


def make_mc_envelope(
    *,
    intent_side: str,
    confidence: float,
    vetoed: bool,
    size: float,
    symbol: str = "UNKNOWN",
    spine_slice_hash: str = "default",
    horizon_minutes: int = 5,
) -> Envelope:
    """
    Drop-in replacement for make_mock_envelope.

    Args:
        horizon_minutes: Prediction horizon in minutes (selects H slice).
                        Longer horizons → more uncertainty in MC simulation.

    Returns just the Envelope (discards provenance for interface parity).
    """
    result = generate_mc_envelope(
        intent_side=intent_side,
        confidence=confidence,
        vetoed=vetoed,
        size=size,
        symbol=symbol,
        spine_slice_hash=spine_slice_hash,
        horizon_minutes=horizon_minutes,
    )
    return result.envelope


__all__ = [
    "generate_mc_envelope",
    "make_mc_envelope",
    "MCEnvelopeResult",
    "MC_KERNEL_VERSION",
]
