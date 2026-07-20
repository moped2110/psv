# ADR-001: Chain-Truth Oracle Design

**Status:** accepted  
**Date:** 2026-07-19  
**Deciders:** Moses Brown, Hermes Agent

## Context
psv needs a second, independent recording of every payment — read directly from the chain — to compare against what the System Under Test (SUT) believes happened.

## Decision
Use a dual-recording architecture:
1. **Chain-Truth:** `eth_getLogs`, `balanceOf`, `authorizationState` — direct RPC reads, no SUT involvement
2. **System-Belief:** HTTP `GET /status/{paymentId}` — what the SUT reports

Any divergence between the two = a bug. The Chain-Truth is authoritative; the SUT is under test.

## Alternatives Considered
- **SUT-only reconciliation:** Rejected — can't find bugs the SUT doesn't know about
- **Shared database:** Rejected — violates independence requirement

## Consequences
- Requires reliable RPC access (Alchemy/Infura/QuickNode)
- Nonce-based settlement identity avoids amount-guessing
- psv runs **read-only** — never signs, never settles, never moves money
