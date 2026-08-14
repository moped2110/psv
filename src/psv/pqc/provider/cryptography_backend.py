"""Primary PQC backend using pyca/cryptography and OpenSSL 3.5+."""

from __future__ import annotations


class CryptographyProvider:
    """Verify classical and ML-DSA signatures through pyca/cryptography."""

    def __init__(self) -> None:
        """Cache runtime capability detection for clear, stable failures."""
        self._reason = self._detect_unavailability()

    @staticmethod
    def _openssl_runtime() -> tuple[int, str]:
        """Return the OpenSSL runtime embedded in pyca, not Python's ssl module."""
        from cryptography.hazmat.backends.openssl.backend import backend

        return backend.openssl_version_number(), backend.openssl_version_text()

    @staticmethod
    def _detect_unavailability() -> str:
        """Return an actionable reason when the required primitive is absent."""
        try:
            version, version_text = CryptographyProvider._openssl_runtime()
            from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PublicKey
        except (ImportError, AttributeError):
            return (
                "ML-DSA-65 is unavailable in pyca/cryptography; install a build exposing "
                "the OpenSSL 3.5 ML-DSA API"
            )
        if version < 0x30500000:
            return f"ML-DSA-65 requires OpenSSL 3.5 or newer; runtime is {version_text}"
        if not callable(getattr(MLDSA65PublicKey, "from_public_bytes", None)):
            return "pyca/cryptography does not expose ML-DSA-65 public-key loading"
        return ""

    @property
    def available(self) -> bool:
        """Return whether OpenSSL and pyca expose ML-DSA-65."""
        return not self._reason

    @property
    def unavailable_reason(self) -> str:
        """Explain why this runtime cannot verify ML-DSA-65."""
        return self._reason

    def verify_ecdsa_p256_sha256(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Verify an encoded-point P-256 public key and DER signature."""
        if not self.available:
            return False
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            return False
        return True

    def verify_mldsa65(
        self,
        public_key: bytes,
        message: bytes,
        signature: bytes,
        *,
        context: bytes = b"",
    ) -> bool:
        """Verify an ML-DSA-65 signature with the FIPS 204 external interface."""
        if not self.available:
            return False
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PublicKey

        try:
            key = MLDSA65PublicKey.from_public_bytes(public_key)
            key.verify(signature, message, context)
        except (InvalidSignature, ValueError):
            return False
        return True
