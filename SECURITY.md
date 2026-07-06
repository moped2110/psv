# Security Policy

`psv` (payment-system-verification) is a **system-level verification harness**. It
compares on-chain truth against a system's own records to surface settlement bugs
(silent loss, phantom credit, reorg/finality, reconciliation gaps). It is **read-only
against real chains** — it never signs or settles on a live rail; outbound value is
Anvil/testnet only. This policy covers vulnerabilities **in the harness itself**.

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

- **In scope:** a way to make the divergence detector emit a false verdict, any code
  path that could sign or settle on a real rail (a money-invariant break), signer-key
  leakage, or code execution / crashes on hostile RPC/log input.
- **Out of scope:** vulnerabilities in the payment systems you point the harness at —
  those belong to that system's own disclosure process.

## Supported versions

The latest version on the default branch is supported. This is a pre-1.0 harness; fixes
land on `main`.
