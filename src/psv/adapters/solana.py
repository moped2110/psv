"""Solana RPC adapter — T-09."""

from __future__ import annotations

import requests


class SolanaAdapter:
    """Read-only Solana RPC adapter. Default: devnet."""

    _rpc_url: str

    def __init__(self, rpc_url: str = "https://api.devnet.solana.com") -> None:
        """Create adapter targeting a Solana JSON-RPC endpoint (default: devnet)."""
        self._rpc_url = rpc_url

    def get_balance(self, address: str) -> int | None:
        """Get SOL balance in lamports. Returns None on failure."""
        try:
            resp = requests.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "getBalance",
                    "params": [address],
                    "id": 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
            if isinstance(result, dict):
                return int(result.get("value", 0))
            return int(result) if result is not None else None
        except requests.RequestException:
            return None
