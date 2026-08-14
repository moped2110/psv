"""Optional liboqs-python verification backend."""

from __future__ import annotations

import importlib
import os
from ctypes.util import find_library
from pathlib import Path
from types import ModuleType


class OQSProvider:
    """Use the optional ``oqs`` package for ML-DSA and pyca for ECDSA."""

    def __init__(self) -> None:
        """Import liboqs lazily so core and primary installations remain isolated."""
        self._oqs: ModuleType | None
        if not self._native_library_present():
            self._oqs = None
            self._reason = (
                "oqs backend unavailable; install psv[pqc-oqs] and the native liboqs library"
            )
            return
        try:
            self._oqs = importlib.import_module("oqs")
            self._reason = ""
        except (ImportError, RuntimeError, SystemExit):
            self._oqs = None
            self._reason = "oqs backend unavailable; native liboqs could not be loaded"

    @staticmethod
    def _native_library_present() -> bool:
        """Detect liboqs without importing a wrapper that may start a source build."""
        if find_library("oqs") or find_library("liboqs"):
            return True
        install_path = os.environ.get("OQS_INSTALL_PATH")
        if not install_path:
            return False
        root = Path(install_path)
        return any(
            (root / directory / filename).is_file()
            for directory in ("lib", "lib64")
            for filename in ("liboqs.so", "liboqs.dylib")
        )

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
