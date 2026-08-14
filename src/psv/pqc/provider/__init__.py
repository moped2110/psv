"""Cryptographic backends for PQC receipt verification.

Imports of optional native libraries are deliberately confined to this package.
"""

from __future__ import annotations

from typing import Protocol

from .cryptography_backend import CryptographyProvider


class ProviderUnavailableError(RuntimeError):
    """Raised when a selected cryptographic backend cannot be used."""


class VerificationProvider(Protocol):
    """Minimal backend contract needed by the receipt verifier."""

    @property
    def available(self) -> bool:
        """Whether all required algorithms are usable in this runtime."""

    @property
    def unavailable_reason(self) -> str:
        """Human-readable capability failure, empty when available."""

    def verify_ecdsa_p256_sha256(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Verify a DER-encoded P-256 ECDSA/SHA-256 signature."""

    def verify_mldsa65(
        self,
        public_key: bytes,
        message: bytes,
        signature: bytes,
        *,
        context: bytes = b"",
    ) -> bool:
        """Verify an external-interface FIPS 204 ML-DSA-65 signature."""


def load_provider(name: str = "cryptography") -> VerificationProvider:
    """Load a backend by its stable configuration name."""
    if name == "cryptography":
        provider: VerificationProvider = CryptographyProvider()
    elif name == "oqs":
        from .oqs_backend import OQSProvider

        provider = OQSProvider()
    else:
        raise ProviderUnavailableError(f"unknown PQC provider: {name}")
    if not provider.available:
        raise ProviderUnavailableError(provider.unavailable_reason)
    return provider


__all__ = [
    "CryptographyProvider",
    "ProviderUnavailableError",
    "VerificationProvider",
    "load_provider",
]
