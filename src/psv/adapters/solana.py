"""Module — see functions for individual docstrings."""
# src/psv/adapters/solana.py
import requests
from typing import Optional

class SolanaAdapter:
    def __init__(self, rpc_url: str = "https://api.devnet.solana.com"):
        self.rpc_url = rpc_url

    def get_balance(self, pubkey: str) -> Optional[int]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [pubkey]}
        try:
            r = requests.post(self.rpc_url, json=payload, timeout=10)
            return r.json().get("result", {}).get("value")
        except Exception:
            return None

