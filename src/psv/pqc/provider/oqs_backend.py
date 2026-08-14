"""Optional liboqs-python verification backend."""

from __future__ import annotations

import importlib
from types import ModuleType


class OQSProvider:
    """Use the optional ``oqs`` package for ML-DSA and pyca for ECDSA."""

    def __init__(self) -> None:
        """Import liboqs lazily so core and primary installations remain isolated."""
        self._oqs: ModuleType | None
        try:
            self._oqs = importlib.import_module("oqs")
            self._reason = ""
        except ImportError:
            self._oqs = None
            self._reason = "oqs backend unavailable; install psv[pqc-oqs]"

    @property
    def available(self) -> bool:
        """Return whether liboqs-python was imported successfully."""
        return self._oqs is not None

    @property
    def unavailable_reason(self) -> str:
        """Explain how to provision the optional backend."""
        return self._reason

    def verify_ecdsa_p256_sha256(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Delegate the classical half to the primary audited backend."""
        from .cryptography_backend import CryptographyProvider

        return CryptographyProvider().verify_ecdsa_p256_sha256(public_key, message, signature)

    def verify_mldsa65(
        self,
        public_key: bytes,
        message: bytes,
        signature: bytes,
        *,
        context: bytes = b"",
    ) -> bool:
        """Verify ML-DSA-65 through liboqs; contexts are not supported there."""
        if self._oqs is None or context:
            return False
        try:
            with self._oqs.Signature("ML-DSA-65") as verifier:
                return bool(verifier.verify(message, signature, public_key))
        except (RuntimeError, ValueError):
            return False
