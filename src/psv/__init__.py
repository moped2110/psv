"""psv — system-level verification harness for complete x402 payment systems.

Where ``01-x402-testsuite`` checks an endpoint's *protocol conformance* from the
outside (one URL, black-box), this harness verifies that the *system behind* the
endpoint pays, books and recovers correctly under real chain conditions:
settlement detection, RPC resilience, idempotency, reorg handling, reconciliation.

Two ingredients are always required:
  1. a System-under-Test (SUT) reachable over a defined HTTP adapter, and
  2. a controllable chain (Anvil) we can snapshot, revert and manipulate.

The harness's core value is the **independent chain-truth oracle**: it reads the
chain directly (balances, nonces, transfer logs) and compares that ground truth
to what the SUT *believes* happened. Any gap is a divergence — and divergences
are exactly the failures (vanished payments, double credits, reorg blindness)
that black-box conformance cannot see.
"""

__version__ = "0.1.0"

# Pinned reference deployment used across the harness.
# Anvil's first account deploys deterministically to this address.
DEFAULT_TOKEN_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
DEFAULT_CHAIN_ID = 84532  # Base-Sepolia semantics, run locally on Anvil
DEFAULT_TOKEN_NAME = "USDC"
DEFAULT_TOKEN_VERSION = "2"
