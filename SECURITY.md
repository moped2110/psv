# Security Policy

`psv` (payment-system-verification) is a **system-level verification harness**. It
compares on-chain truth against a system's own records to surface settlement bugs
(silent loss, phantom credit, reorg/finality, reconciliation gaps). It is **read-only
against real chains** — it never signs or settles on a live rail; outbound value is
Anvil/testnet only. This policy covers vulnerabilities **in the harness itself**.

## Verification safety invariant

psv reads chains; it does not pay on them. There is no signing path in the verifier,
and a verdict is produced entirely from observation.

**The one place value moves is the reference SUT**, the test harness that plays the
role of a paying system so the detectors have something to grade. Every submission
from it passes a central fail-closed policy (`psv.safety`) before a transaction is
constructed or signed:

- The chain must be one of an explicit local/testnet allowlist — `1337`, `31337`,
  Base Sepolia `84532`, Ethereum Sepolia `11155111`. **There is no runtime override.**
  Adding a chain is a reviewed source change with regression tests, not configuration.
- The chain is **observed**, not trusted: `eth_chainId` is read from the RPC actually
  in use and must match, so a misconfigured endpoint fails closed rather than
  submitting somewhere else.
- The token address must be a well-formed, non-zero EVM address with deployed
  bytecode (`eth_getCode`). Settling against an account with no code would be a
  silent no-op.
- The authorization amount must equal the quoted order amount exactly, and lie within
  uint256 payment bounds.

**Chain truth is read from the configured host only.** The JSON-RPC transport refuses
redirects. A verdict is worth exactly as much as the chain it was read from, so a
redirecting or hijacked provider must not be able to move the read to a host the
operator did not configure — and following one would also permit an https-to-http
downgrade.

**The RPC endpoint does not travel in errors.** Hosted providers put the API key in
the URL path, so errors render the endpoint as scheme and host only. The MCP surface
returns a verdict and sends the detail to the operator's log.

## Reporting a vulnerability

Please report privately — do **not** open a public issue for a security bug.

- Preferred: open a **GitHub private security advisory** on this repository
  (repo → *Security* → *Report a vulnerability*).
- The advisory is private to the maintainer until a fix is released.

Include: affected version, a minimal reproduction, and the impact you observed.

## What to expect

- Acknowledgement within a few days.
- A fix or mitigation plan, and coordinated disclosure once a fix is available.
- Credit in the release notes if you would like it.

## Scope notes

- **In scope:** a way to make the divergence detector emit a false verdict — including
  substituting the chain it reads from — any code path that could sign or settle on a
  real rail (a money-invariant break), leakage of a signer key or of the RPC provider
  credential carried in `PSV_RPC_URL`, or code execution / crashes on hostile RPC/log
  input.
- **Out of scope:** vulnerabilities in the payment systems you point the harness at —
  those belong to that system's own disclosure process.

## Supported versions

The latest version on the default branch is supported. This is a pre-1.0 harness; fixes
land on `main`.
