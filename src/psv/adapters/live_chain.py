"""Alchemy/Infura RPC adapter with rate-limiting — T-06."""
from __future__ import annotations

import time
from typing import Any

import requests


class LiveChainAdapter:
    """Read-only RPC adapter with rate-limiting."""
    _rpc_url: str
    _calls_per_second: float
    _last_call: float

    def __init__(self, rpc_url: str, calls_per_second: float = 10.0) -> None:
        self._rpc_url = rpc_url
        self._calls_per_second = calls_per_second
        self._last_call = 0.0

    def _rate_limit(self) -> None:
        """Sleep if needed to stay within rate limit."""
        elapsed = time.time() - self._last_call
        min_gap = 1.0 / self._calls_per_second
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_call = time.time()

    def call(self, method: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        """Make a JSON-RPC call. Returns None on failure."""
        self._rate_limit()
        try:
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or [],
                "id": 1,
            }
            resp = requests.post(self._rpc_url, json=payload, timeout=30)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            if "error" in data:
                return None
            return data
        except requests.RequestException:
            return None
