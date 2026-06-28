"""Offline tests for the rail registry + read-only reconciliation (multi-asset).

No chain: the ``TokenView`` runs over an injected fake JSON-RPC transport that
canned-answers ``balanceOf`` / ``authorizationState`` from the call selector.
"""

from __future__ import annotations

from typing import Any

from psv.anvil import RpcClient
from psv.chain import TokenView
from psv.divergence import DivergenceKind
from psv.rails import KNOWN_RAILS, get_rail, reconcile_live, token_for_rail

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20

_SEL_BALANCE_OF = "70a08231"
_SEL_AUTH_STATE = "e94a0102"


def _fake_transport(balances: dict[str, int], nonce_used: bool):  # type: ignore[no-untyped-def]
    def transport(req: dict[str, Any]) -> dict[str, Any]:
        result = "0x0"
        if req["method"] == "eth_call":
            data = req["params"][0]["data"]
            selector = data[2:10]
            if selector == _SEL_BALANCE_OF:
                who = ("0x" + data[-40:]).lower()
                result = hex(balances.get(who, 0))
            elif selector == _SEL_AUTH_STATE:
                result = hex(1 if nonce_used else 0)
        return {"jsonrpc": "2.0", "id": req["id"], "result": result}

    return transport


def _token(balances: dict[str, int], nonce_used: bool) -> TokenView:
    rpc = RpcClient(transport=_fake_transport(balances, nonce_used))
    return token_for_rail(get_rail("eurc-base"), rpc)


# --- registry -------------------------------------------------------------


def test_known_rails_cover_usdc_jpyc_eurc() -> None:
    assert set(KNOWN_RAILS) >= {"mock-anvil", "usdc-base", "jpyc-polygon", "eurc-base"}


def test_eurc_is_the_eur_rail() -> None:
    eurc = get_rail("eurc-base")
    assert eurc.token_address == "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42"
    assert eurc.chain_id == 8453
    assert eurc.decimals == 6
    # domain intentionally unset until verified on-chain (read-only doesn't sign)
    assert eurc.token_name is None and eurc.token_version is None


def test_decimals_contrast_is_the_damage_case() -> None:
    # 6-vs-18 is itself the decimals bug class: USDC/EURC are 6, JPYC is 18.
    assert get_rail("usdc-base").decimals == 6
    assert get_rail("eurc-base").decimals == 6
    assert get_rail("jpyc-polygon").decimals == 18
    assert get_rail("jpyc-polygon").token_name == "JPY Coin"


def test_unknown_rail_raises() -> None:
    try:
        get_rail("nope")
    except KeyError as e:
        assert "nope" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for an unknown rail")


# --- read-only reconciliation --------------------------------------------


def test_consistent_paid() -> None:
    token = _token({PAYER.lower(): 900, PAYEE.lower(): 100}, nonce_used=True)
    d = reconcile_live(
        token, payer=PAYER, payee=PAYEE, nonce="0x" + "ab" * 32,
        payer_before=1000, payee_before=0, sut_believes_paid=True,
    )
    assert d.kind is DivergenceKind.CONSISTENT_PAID and not d.is_failure


def test_phantom_credit_caught() -> None:
    # System believes paid, but nothing moved on-chain (nonce free, balances flat).
    token = _token({PAYER.lower(): 1000, PAYEE.lower(): 0}, nonce_used=False)
    d = reconcile_live(
        token, payer=PAYER, payee=PAYEE, nonce="0x" + "ab" * 32,
        payer_before=1000, payee_before=0, sut_believes_paid=True,
    )
    assert d.kind is DivergenceKind.PHANTOM_CREDIT and d.is_failure


def test_silent_loss_caught() -> None:
    # Funds moved on-chain, but the system thinks the order is unpaid.
    token = _token({PAYER.lower(): 900, PAYEE.lower(): 100}, nonce_used=True)
    d = reconcile_live(
        token, payer=PAYER, payee=PAYEE, nonce="0x" + "ab" * 32,
        payer_before=1000, payee_before=0, sut_believes_paid=False,
    )
    assert d.kind is DivergenceKind.SILENT_LOSS and d.is_failure
