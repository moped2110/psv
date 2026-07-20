"""Module — see functions for individual docstrings."""
# src/psv/adapters/live_chain.py
import time
import requests
from typing import Optional

class LiveChainAdapter:
    def __init__(self, rpc_url: str, max_retries: int = 3, rate_limit: float = 0.2):
        self.rpc_url = rpc_url
        self.max_retries = max_retries
        self.rate_limit = rate_limit
        self.last_call = 0.0

    def _rate_limit(self):
        now = time.time()
        if now - self.last_call < self.rate_limit:
            time.sleep(self.rate_limit - (now - self.last_call))
        self.last_call = time.time()

    def call(self, method: str, params: list) -> Optional[dict]:
        self._rate_limit()
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        for attempt in range(self.max_retries):
            try:
                r = requests.post(self.rpc_url, json=payload, timeout=10)
                r.raise_for_status()
                return r.json()
            except Exception:
                if attempt == self.max_retries - 1:
                    return None
                time.sleep(2 ** attempt)
        return None

