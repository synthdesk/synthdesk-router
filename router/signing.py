"""
Cryptographic signing for authority certificates.

Uses Ed25519 for certificate signing and verification.
Private key is held offline by operator. Public key is embedded in repo.

This replaces the self-hash "integrity" check with real cryptographic anchoring.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Ed25519 signing - REQUIRED for certificate verification
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Ed25519PrivateKey = None
    Ed25519PublicKey = None


# Public key location (loaded from file at import time)
_PUBLIC_KEY_FILE = Path(__file__).parent / "public_key.b64"


def _load_embedded_public_key() -> Optional[str]:
    """Load public key from committed file."""
    if _PUBLIC_KEY_FILE.exists():
        return _PUBLIC_KEY_FILE.read_text().strip()
    return None


# Load at import time - None means signing infrastructure not deployed
EMBEDDED_PUBLIC_KEY_B64 = _load_embedded_public_key()


def generate_keypair() -> Tuple[str, str]:
    """
    Generate a new Ed25519 keypair.

    Returns:
        (private_key_b64, public_key_b64)

    The private key should be stored securely offline.
    The public key should be written to packages/router/router/public_key.b64
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library not installed")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    private_b64 = base64.b64encode(private_bytes).decode("ascii")
    public_b64 = base64.b64encode(public_bytes).decode("ascii")

    print(f"Private key (KEEP OFFLINE): {private_b64}")
    print(f"Public key (embed in repo): {public_b64}")

    return private_b64, public_b64


def load_private_key(private_key_b64: str) -> "Ed25519PrivateKey":
    """Load private key from base64."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library not installed")

    private_bytes = base64.b64decode(private_key_b64)
    return Ed25519PrivateKey.from_private_bytes(private_bytes)


def load_public_key(public_key_b64: str) -> "Ed25519PublicKey":
    """Load public key from base64."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library not installed")

    public_bytes = base64.b64decode(public_key_b64)
    return Ed25519PublicKey.from_public_bytes(public_bytes)


def compute_cert_payload_hash(cert: Dict[str, Any]) -> bytes:
    """
    Compute canonical hash of certificate payload (excluding signature).

    The signed payload excludes:
    - cert_sig (the signature itself)
    - cert_sha256 (legacy self-hash, deprecated)
    """
    # Extract only the fields that are signed
    signed_fields = {k: v for k, v in cert.items() if k not in ("cert_sig", "cert_sha256")}
    canonical = json.dumps(signed_fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def sign_certificate(cert: Dict[str, Any], private_key_b64: str) -> str:
    """
    Sign a certificate with the private key.

    Args:
        cert: Certificate dict (without signature)
        private_key_b64: Base64-encoded Ed25519 private key

    Returns:
        Base64-encoded signature to store in cert["cert_sig"]
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library not installed")

    private_key = load_private_key(private_key_b64)
    payload_hash = compute_cert_payload_hash(cert)
    signature = private_key.sign(payload_hash)
    return base64.b64encode(signature).decode("ascii")


def verify_certificate_signature(
    cert: Dict[str, Any],
    public_key_b64: Optional[str] = None,
) -> bool:
    """
    Verify certificate signature.

    Args:
        cert: Certificate dict with cert_sig field
        public_key_b64: Base64-encoded public key (defaults to embedded key)

    Returns:
        True if signature is valid, False otherwise
    """
    if not CRYPTO_AVAILABLE:
        # If crypto not available, fail closed
        return False

    # Get signature from cert
    sig_b64 = cert.get("cert_sig")
    if not sig_b64:
        return False

    # Use embedded key if not provided
    if public_key_b64 is None:
        public_key_b64 = EMBEDDED_PUBLIC_KEY_B64

    if not public_key_b64:
        # No embedded key configured
        return False

    try:
        public_key = load_public_key(public_key_b64)
        signature = base64.b64decode(sig_b64)
        payload_hash = compute_cert_payload_hash(cert)
        public_key.verify(signature, payload_hash)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def sign_certificate_file(
    cert_path: Path,
    private_key_b64: str,
    output_path: Optional[Path] = None,
) -> None:
    """
    Sign a certificate file and write the signed version.

    Args:
        cert_path: Path to unsigned certificate JSON
        private_key_b64: Base64-encoded Ed25519 private key
        output_path: Output path (defaults to overwriting input)
    """
    with cert_path.open("r", encoding="utf-8") as f:
        cert = json.load(f)

    # Remove old self-hash if present
    cert.pop("cert_sha256", None)
    cert.pop("cert_sig", None)

    # Sign
    cert["cert_sig"] = sign_certificate(cert, private_key_b64)

    # Write
    out = output_path or cert_path
    with out.open("w", encoding="utf-8") as f:
        json.dump(cert, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Signed certificate written to: {out}")
