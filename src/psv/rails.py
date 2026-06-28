"""Rail registry — parameterise the harness for real EIP-3009 stablecoin rails.

The chain-truth oracle (:class:`psv.chain.TokenView`) and the divergence detector
are already rail-agnostic: ``balanceOf``, ``authorizationState`` and the
``AuthorizationUsed`` log are standard across every EIP-3009 token. This module
pins the *constants* for the rails we care about so the same harness can reconcile
USDC, JPYC, or EURC — the latter being the MiCA-era EUR rail (Circle's EURC
implements EIP-3009 natively and rides the same ``exact/eip3009`` path as USDC,
supported by the CDP facilitator).

**Read-only / money invariant.** The reconciliation here only *reads* the chain
(balances + nonce state) and compares it against a system's belief. psv never signs
or settles on a real rail — outbound value is testnet/Anvil only. So a
``RailConfig``'s EIP-712 domain (``token_name``/``token_version``) is needed only
for the local Anvil *signing* path; live read-only reconciliation ignores it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anvil import RpcClient
from .chain import TokenView
from .divergence import Divergence, detect_payment_divergence, settlement_truth_from_balances


@dataclass(frozen=True)
class RailConfig:
    """A named EIP-3009 stablecoin rail.

    ``token_address`` + ``chain_id`` are load-bearing for read-only reconciliation.
    ``decimals`` is informational (human amounts / the decimals damage case — a
    6-vs-18 assumption is itself a bug class: USDC/EURC are 6, JPYC is 18).
    ``token_name``/``token_version`` are the EIP-712 domain, needed only for the
    signing path; ``None`` means "verify on-chain before using this rail to sign".
    """

    key: str
    label: str
    chain_id: int
    token_address: str
    decimals: int
    token_name: str | None = None
    token_version: str | None = None


# Addresses verified on the respective explorers. Domains are filled only where
# verified; an unset domain is fine because read-only reconciliation never signs.
KNOWN_RAILS: dict[str, RailConfig] = {
    "mock-anvil": RailConfig(
        "mock-anvil", "Local MockUSDC (Anvil)", 84532,
        "0x5FbDB2315678afecb367f032d93F642f64180aa3", 6, "USDC", "2",
    ),
    "usdc-base": RailConfig(
        "usdc-base", "USDC on Base", 8453,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6, "USD Coin", "2",
    ),
    "jpyc-polygon": RailConfig(
        "jpyc-polygon", "JPYC on Polygon", 137,
        "0xe7c3d8c9a439fede00d2600032d5db0be71c3c29", 18, "JPY Coin", "1",
    ),
    "eurc-base": RailConfig(
        # EURC implements EIP-3009 natively and is supported by the CDP facilitator,
        # so it rides the same exact/eip3009 path as USDC — the MiCA-era EUR rail.
        # Domain intentionally unset: verify name/version on-chain before signing.
        "eurc-base", "EURC on Base", 8453,
        "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42", 6,
    ),
}


def get_rail(key: str) -> RailConfig:
    """Look up a known rail by key, or raise with the list of known keys."""
    try:
        return KNOWN_RAILS[key]
    except KeyError:
        raise KeyError(f"unknown rail {key!r}; known: {', '.join(sorted(KNOWN_RAILS))}") from None


def token_for_rail(rail: RailConfig, rpc: RpcClient) -> TokenView:
    """A read handle to ``rail``'s token over ``rpc`` (the read-only oracle)."""
    return TokenView(rpc=rpc, address=rail.token_address)


def reconcile_live(
    token: TokenView,
    *,
    payer: str,
    payee: str,
    nonce: str,
    payer_before: int,
    payee_before: int,
    sut_believes_paid: bool,
) -> Divergence:
    """Read-only reconciliation of one payment against the system's belief.

    Reads the *current* on-chain truth — whether the EIP-3009 nonce was consumed
    and the payer/payee balances — and compares it to whether the system thinks it
    was paid, returning a :class:`psv.divergence.Divergence`. Moves no funds: psv
    never signs or settles on a real rail. ``payer_before``/``payee_before`` are the
    pre-payment balance snapshots the caller captured.
    """
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer, nonce),
        payer_before=payer_before,
        payer_after=token.balance_of(payer),
        payee_before=payee_before,
        payee_after=token.balance_of(payee),
    )
    return detect_payment_divergence(truth, sut_believes_paid)
