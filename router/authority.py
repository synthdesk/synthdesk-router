"""
Router authority management.

Handles:
- Authority level enum (v0.1, v0.2, v0.3, v1.0)
- Certificate loading and verification
- Runtime authority binding
- Continuous demotion monitoring
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def compute_cert_body_sha256(cert: Dict[str, Any]) -> str:
    """
    Compute canonical SHA256 of certificate body (excluding signature fields).

    This provides a stable identity hash for the certificate that works
    regardless of whether the cert uses Ed25519 signature or legacy self-hash.

    Args:
        cert: Certificate dict

    Returns:
        Hex-encoded SHA256 hash of canonical cert body
    """
    # Exclude signature/hash fields - hash only the semantic content
    body_fields = {k: v for k, v in cert.items() if k not in ("cert_sig", "cert_sha256")}
    canonical = json.dumps(body_fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_prefix(s: Optional[str], length: int = 16) -> str:
    """Safely get prefix of string, returning 'none' if None."""
    return s[:length] if s else "none"


class AuthorityLevel(Enum):
    """
    Router authority tiers.

    v0.1: Observational (shadow-only)
    v0.2: Inbox-authoritative (no execution)
    v0.3: Bounded execution (within risk envelope)
    v1.0: Full operational
    """

    V0_1 = "v0.1"
    V0_2 = "v0.2"
    V0_3 = "v0.3"
    V1_0 = "v1.0"

    def __str__(self) -> str:
        return self.value

    def __lt__(self, other: "AuthorityLevel") -> bool:
        order = [AuthorityLevel.V0_1, AuthorityLevel.V0_2, AuthorityLevel.V0_3, AuthorityLevel.V1_0]
        return order.index(self) < order.index(other)

    def __le__(self, other: "AuthorityLevel") -> bool:
        return self == other or self < other

    @property
    def can_emit_non_flat(self) -> bool:
        """Whether this level permits non-flat intents."""
        return self >= AuthorityLevel.V0_2

    @property
    def can_emit_to_inbox(self) -> bool:
        """Whether this level permits emission to durable inbox."""
        return self >= AuthorityLevel.V0_2

    @property
    def can_execute(self) -> bool:
        """Whether this level permits execution."""
        return self >= AuthorityLevel.V0_3


@dataclass
class DemotionEvent:
    """Record of an authority demotion."""

    timestamp: str
    from_level: AuthorityLevel
    to_level: AuthorityLevel
    trigger: str
    details: Optional[str] = None


@dataclass
class AuthorityState:
    """
    Runtime authority state.

    Invariants:
    - Level can only stay same or decrease within session (I1)
    - Demotion events are recorded durably (I3)
    - Promotion requires restart with valid certificate (I4)
    """

    level: AuthorityLevel = AuthorityLevel.V0_1
    cert_path: Optional[Path] = None
    cert_body_sha256: Optional[str] = None  # Always computed from cert body (not legacy field)
    build_meta_sha256: Optional[str] = None
    promoted_at: Optional[str] = None  # ISO timestamp from cert, marks authority epoch start
    demotions: List[DemotionEvent] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def demote(self, trigger: str, details: Optional[str] = None) -> None:
        """
        Demote to v0.1 (lowest level).

        Per I1 (Authority Monotonicity), authority can only decrease.
        """
        if self.level == AuthorityLevel.V0_1:
            logger.warning(f"Already at v0.1, cannot demote further. Trigger: {trigger}")
            return

        event = DemotionEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_level=self.level,
            to_level=AuthorityLevel.V0_1,
            trigger=trigger,
            details=details,
        )
        self.demotions.append(event)
        logger.warning(f"AUTHORITY DEMOTION: {self.level} -> v0.1 | trigger={trigger}")
        self.level = AuthorityLevel.V0_1

    def is_demoted(self) -> bool:
        """Check if any demotion has occurred."""
        return len(self.demotions) > 0


class CertificateError(Exception):
    """Certificate verification failed."""

    pass


def load_certificate(cert_path: Path) -> Dict[str, Any]:
    """
    Load and parse promotion certificate.

    Raises:
        CertificateError: If certificate is missing or invalid
    """
    if not cert_path.exists():
        raise CertificateError(f"Certificate not found: {cert_path}")

    try:
        with cert_path.open("r", encoding="utf-8") as f:
            cert = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        raise CertificateError(f"Certificate parse error: {e}")

    return cert


def verify_certificate_integrity(cert: Dict[str, Any], allow_legacy: bool = False) -> bool:
    """
    Verify certificate cryptographic integrity.

    REQUIRES Ed25519 signature unless allow_legacy=True (dev-only flag).

    Args:
        cert: Certificate dict
        allow_legacy: If True, accept deprecated self-hash (DANGEROUS, dev only)

    Returns:
        True if signature is valid, False otherwise
    """
    # Ed25519 signature is REQUIRED for v0.2 binding
    if "cert_sig" in cert:
        try:
            from router.signing import verify_certificate_signature
            return verify_certificate_signature(cert)
        except ImportError:
            logger.error("Signature verification unavailable (cryptography not installed)")
            return False

    # No signature - fail closed unless legacy explicitly allowed
    if not allow_legacy:
        logger.error("Certificate missing required cert_sig. Unsigned certificates rejected.")
        logger.error("Use --allow-legacy-cert flag for development only.")
        return False

    # Legacy self-hash (DEPRECATED - requires explicit opt-in)
    stored_hash = cert.get("cert_sha256")
    if not stored_hash:
        logger.warning("Certificate has neither cert_sig nor cert_sha256")
        return False

    logger.warning("DANGER: Using DEPRECATED self-hash verification (--allow-legacy-cert).")
    logger.warning("This is insecure. Production certificates MUST be signed with Ed25519.")
    cert_for_hash = {k: v for k, v in cert.items() if k not in ("cert_sha256", "cert_sig")}
    canonical = json.dumps(cert_for_hash, sort_keys=True, separators=(",", ":"))
    computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return computed_hash == stored_hash


def verify_build_meta_match(cert: Dict[str, Any], current_build_meta: Dict[str, Any]) -> bool:
    """
    Verify certificate build_meta_sha256 matches current code.

    Args:
        cert: Certificate dict
        current_build_meta: Current ROUTER_BUILD_META.json contents

    Returns:
        True if hashes match, False otherwise
    """
    cert_hash = cert.get("build_meta_sha256")
    current_hash = current_build_meta.get("combined_sha256")

    if not cert_hash or not current_hash:
        return False

    return cert_hash == current_hash


def bind_authority(
    cert_path: Optional[Path] = None,
    current_build_meta: Optional[Dict[str, Any]] = None,
    allow_legacy_cert: bool = False,
) -> AuthorityState:
    """
    Bind authority level at router startup.

    Implements startup logic from v0_1_to_v0_2.md:
    1. Look for PROMOTION_CERT_v0_2.json
    2. If not found -> v0.1
    3. If found: verify Ed25519 signature + build meta match
    4. If verification fails -> v0.1 + warning
    5. If verification passes -> v0.2

    SECURITY: Certificates MUST be Ed25519 signed. Legacy self-hash is
    only accepted with allow_legacy_cert=True (development only).

    Args:
        cert_path: Path to promotion certificate (or None for v0.1)
        current_build_meta: Current ROUTER_BUILD_META.json contents
        allow_legacy_cert: Accept deprecated self-hash certs (DANGEROUS, dev only)

    Returns:
        AuthorityState initialized at appropriate level
    """
    state = AuthorityState()

    # No certificate -> v0.1
    if not cert_path:
        logger.info("No certificate provided. Starting at v0.1 (shadow-only).")
        return state

    # Try to load certificate
    try:
        cert = load_certificate(cert_path)
    except CertificateError as e:
        logger.warning(f"Certificate load failed: {e}. Starting at v0.1.")
        return state

    # Verify certificate version
    cert_version = cert.get("cert_version")
    if cert_version != "v0.2":
        logger.warning(f"Unknown certificate version: {cert_version}. Starting at v0.1.")
        return state

    # Verify certificate integrity (REQUIRES Ed25519 signature by default)
    if not verify_certificate_integrity(cert, allow_legacy=allow_legacy_cert):
        logger.warning("Certificate integrity check failed. Starting at v0.1.")
        return state

    # Verify build meta match
    if current_build_meta:
        if not verify_build_meta_match(cert, current_build_meta):
            logger.warning(
                "Certificate build_meta_sha256 does not match current code. "
                "Code has changed since promotion. Starting at v0.1."
            )
            return state
    else:
        logger.warning("No current build meta provided. Cannot verify code match. Starting at v0.1.")
        return state

    # All checks passed -> v0.2
    state.level = AuthorityLevel.V0_2
    state.cert_path = cert_path
    # Always compute cert_body_sha256 from cert content (works for both signed and legacy)
    state.cert_body_sha256 = compute_cert_body_sha256(cert)
    state.build_meta_sha256 = cert.get("build_meta_sha256")
    state.promoted_at = cert.get("promoted_at")  # Authority epoch start

    logger.info("Certificate verified. Starting at v0.2 (inbox-authoritative).")
    logger.info(f"  cert_body_sha256: {_safe_prefix(state.cert_body_sha256)}...")
    logger.info(f"  build_meta_sha256: {_safe_prefix(state.build_meta_sha256)}...")
    logger.info(f"  promoted_at: {state.promoted_at or 'none (all violations count)'}")

    return state


class DemotionWatcher:
    """
    Continuous demotion monitoring.

    Monitors conditions that trigger demotion back to v0.1.
    Called after each event processing to check triggers.
    """

    def __init__(self, authority_state: AuthorityState):
        self.authority_state = authority_state
        self._checks: List[Callable[[], Optional[str]]] = []

    def add_check(self, check: Callable[[], Optional[str]]) -> None:
        """
        Add a demotion check.

        The check callable should return None if OK,
        or a trigger description string if demotion needed.
        """
        self._checks.append(check)

    def check_all(self) -> None:
        """
        Run all demotion checks.

        If any check returns a trigger, demote immediately.
        """
        if self.authority_state.level == AuthorityLevel.V0_1:
            # Already at lowest level, nothing to demote
            return

        for check in self._checks:
            trigger = check()
            if trigger:
                self.authority_state.demote(trigger)
                # Don't need to check more - already demoted
                return


def create_violation_active_check(get_violation_active: Callable[[], bool]) -> Callable[[], Optional[str]]:
    """
    Create a demotion check for violation_active state.

    Per v0_1_to_v0_2.md: "violation_active becomes true" triggers demotion.
    """

    def check() -> Optional[str]:
        if get_violation_active():
            return "violation_active_true"
        return None

    return check


def create_build_meta_check(
    cert_build_meta_sha256: str,
    get_current_build_meta_sha256: Callable[[], str],
) -> Callable[[], Optional[str]]:
    """
    Create a demotion check for build meta mismatch.

    Per v0_1_to_v0_2.md: "Build meta mismatch" triggers demotion.
    """

    def check() -> Optional[str]:
        current = get_current_build_meta_sha256()
        if current != cert_build_meta_sha256:
            return f"build_meta_mismatch (cert={cert_build_meta_sha256[:8]}..., current={current[:8]}...)"
        return None

    return check
