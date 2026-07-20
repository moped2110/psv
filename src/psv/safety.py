import os
import os
from typing import Dict

TEST_NETWORKS: Dict[str, str] = {
    "eip155:5": "Goerli",
    "eip155:11155111": "Sepolia",
    "eip155:80001": "Mumbai",
    "eip155:421613": "Arbitrum Goerli",
    "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1": "Solana Devnet",
    "stellar:testnet": "Stellar Testnet",
}

MAINNET_CHAIN_IDS: Dict[str, str] = {
    "eip155:1": "Ethereum",
    "eip155:10": "Optimism",
    "eip155:137": "Polygon",
    "eip155:42161": "Arbitrum",
    "eip155:43114": "Avalanche",
    "eip155:8453": "Base",
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "Solana",
    "stellar:pubnet": "Stellar",
}


def validate_rpc_url(url: str) -> bool:
    """Return True if the RPC URL is known to point to a testnet."""
    if not url:
        return False
    for net_id, label in TEST_NETWORKS.items():
        if net_id.split(":")[1] in url or label.lower() in url.lower():
            return True
    return False


def is_mainnet_network(network: str) -> bool:
    """Return True if the given network identifier is a known mainnet."""
    return network in MAINNET_CHAIN_IDS


def guard_mainnet(network: str, rpc_url: str | None = None) -> None:
    """Raise if mainnet is detected unless PSV_ALLOW_MAINNET=1."""
    if os.environ.get("PSV_ALLOW_MAINNET") == "1":
        return
    if is_mainnet_network(network):
        label = MAINNET_CHAIN_IDS[network]
        msg = f"MAINNET DETECTED: {label} ({network}). Set PSV_ALLOW_MAINNET=1 to override."
        if rpc_url:
            msg += f" RPC: {rpc_url}"
        raise RuntimeError(msg)
