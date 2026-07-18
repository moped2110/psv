"""The reference SUT as a FastAPI app: quote -> pay -> (event-confirm) -> resource.

A faithful miniature of a real x402 payment system:
  * issues a quote (price, merchant address, token) with a validity window,
  * acts as facilitator (submits the payer's signed EIP-3009 authorization),
  * **confirms settlement by watching the token's Transfer event** (the brittle
    bit SC1 targets), and
  * only then unlocks the paid resource.

Configurable weaknesses let the harness exercise the system-level damage cases:
  * **D3** - ledger backup/restore + optional ``reconcile`` job (silent loss on restore).
  * **G3** - a quote locks a price against a movable fair-value oracle (free option).
  * **I**  - idempotency: without it, re-paying a settled order re-submits on-chain.
  * **delay** - confirming a settlement before it is mined yields a false negative.

Run on a dev machine alongside Anvil (needs [sut] + [chain] extras). The harness
talks to it solely over HTTP (or in-process), so any implementation can replace it.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from eth_account import Account

from ..anvil import RpcClient
from ..chain import TokenView
from ..quote_option import quote_is_stale
from ..reconciliation import (
    TOPIC_TRANSFER,
    OnChainCredit,
    SettlementIdentity,
    find_unreconciled,
    topic_addr,
)
from ..safety import DEFAULT_SETTLEMENT_SAFETY_POLICY
from .confirmer import TOPIC_TRANSFER as CONFIRMER_TOPIC
from .confirmer import EventWatchingConfirmer


@dataclass
class SutConfig:
    token_address: str
    merchant_address: str  # payTo - where settlements must land
    facilitator_key: str  # Anvil account key that submits txs (test-only)
    chain_id: int = 84532
    rpc_endpoint: str = "http://127.0.0.1:8545"
    token_name: str = "USDC"
    token_version: str = "2"
    price: int = 10_000  # 0.01 USDC (6 decimals)
    gas: int = 300_000
    # D3 - reconciliation. Off by default: that is the vulnerable system.
    reconciliation_enabled: bool = False
    # G3 - quote lifecycle. Long TTL + no reprice = free option.
    quote_ttl: int = 3600
    reprice_on_pay: bool = False
    reprice_tolerance: float = 0.02
    # I-class - idempotent pay. Off by default: re-paying a settled order
    # re-submits on-chain (vulnerable). On: re-pay returns the cached settlement.
    idempotent_pay: bool = False
    # Settlement-delay handling. Off by default the SUT waits for the settlement
    # tx to be mined before confirming. On (vulnerable) it checks the event
    # immediately, so a not-yet-mined settlement looks unpaid (false negative).
    confirm_without_waiting: bool = False


@dataclass
class _Order:
    order_id: str
    amount: int
    expires_at: int
    quoted_fair_price: int
    nonce_seen: str | None = None
    paid: bool = False
    submitted_tx: str | None = None
    resource: str | None = None
    created_block: int = 0
    recovered: bool = False  # set by reconciliation
    settle_attempts: int = 0  # how many times settlement was submitted on-chain
    settlement_identity: SettlementIdentity | None = None


@dataclass
class ReferenceSut:
    """Holds state + on-chain plumbing; ``create_app`` wraps it in FastAPI."""

    config: SutConfig
    orders: dict[str, _Order] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Wire strict RPC, token, facilitator, oracle, and confirmer dependencies."""
        self.rpc = RpcClient(endpoint=self.config.rpc_endpoint)
        self.token = TokenView(self.rpc, self.config.token_address)
        self.account = Account.from_key(self.config.facilitator_key)
        # Movable fair-value oracle (G3). Starts at the list price.
        self.fair_price = self.config.price
        # The brittle confirmer, wired to real eth_getLogs (legacy Transfer topic0).
        self.confirmer = EventWatchingConfirmer(
            fetch_logs=lambda addr, topics, from_block: self.rpc.get_logs(
                address=addr, topics=topics, from_block=from_block
            ),
            watched_topic0=CONFIRMER_TOPIC,
        )

    # --- domain operations ----------------------------------------------------

    def quote(self) -> dict[str, Any]:
        """Create and persist a priced x402 order with a bounded validity window."""
        order_id = "ord_" + secrets.token_hex(8)
        now = int(time.time())
        order = _Order(
            order_id=order_id,
            amount=self.config.price,
            expires_at=now + self.config.quote_ttl,
            quoted_fair_price=self.fair_price,
            created_block=self.rpc.block_number(),
        )
        self.orders[order_id] = order
        return {
            "order_id": order_id,
            "amount": str(order.amount),
            "payTo": self.config.merchant_address,
            "asset": self.config.token_address,
            "network": f"eip155:{self.config.chain_id}",
            "extra": {"name": self.config.token_name, "version": self.config.token_version},
            "expires_at": order.expires_at,
        }

    def pay(self, order_id: str, authorization: dict[str, Any]) -> dict[str, Any]:
        """Validate order state, submit its authorization, and confirm settlement."""
        order = self.orders.get(order_id)
        if order is None:
            return {"order_id": order_id, "settled": False, "reason": "unknown_order"}

        # I-class - idempotency. A robust system never settles the same order twice;
        # re-pay returns the cached result. The vulnerable default falls through and
        # re-submits on-chain (wasting gas, and double-crediting in a naive ledger).
        if order.paid and self.config.idempotent_pay:
            return {
                "order_id": order_id,
                "settled": True,
                "submitted_tx": order.submitted_tx,
                "idempotent": True,
            }

        # G3 guards - refuse to settle an expired or stale (under-priced) quote.
        now = int(time.time())
        if now > order.expires_at:
            return {"order_id": order_id, "settled": False, "reason": "quote_expired"}
        if self.config.reprice_on_pay and quote_is_stale(
            order.amount, self.fair_price, self.config.reprice_tolerance
        ):
            return {"order_id": order_id, "settled": False, "reason": "stale_quote"}

        order.nonce_seen = str(authorization["nonce"])
        order.settle_attempts += 1
        tx_hash = self._submit_settlement(authorization)
        order.submitted_tx = tx_hash
        # Wait for the settlement to be mined before confirming. Skipping this
        # (the vulnerable mode) checks too early and reports a false "unpaid".
        receipt: dict[str, Any] | None = None
        if not self.config.confirm_without_waiting:
            receipt = self.rpc.wait_for_receipt(tx_hash)
        # Confirm by watching the legacy Transfer event - the SC1-vulnerable step.
        settlement_log_index = self.confirmer.settlement_log_index(
            token=self.config.token_address,
            payer=str(authorization["from"]),
            payee=self.config.merchant_address,
            expected_value=order.amount,
            authorization_nonce=str(authorization["nonce"]),
            submitted_tx=tx_hash,
            receipt=receipt,
            from_block=order.created_block,
        )
        settled = settlement_log_index is not None
        if settlement_log_index is not None:
            order.paid = True
            order.resource = f"premium-content::{order_id}"
            order.settlement_identity = SettlementIdentity(
                chain_id=self.config.chain_id,
                asset=self.config.token_address,
                tx_hash=tx_hash,
                log_index=settlement_log_index,
            )
        return {"order_id": order_id, "submitted_tx": tx_hash, "settled": settled}

    def status(self, order_id: str) -> dict[str, Any]:
        """Return current payment and resource state without mutating the order."""
        order = self.orders.get(order_id)
        if order is None:
            return {
                "order_id": order_id,
                "paid": False,
                "resource": None,
                "submitted_tx": None,
                "known": False,
            }
        return {
            "order_id": order_id,
            "paid": order.paid,
            "resource": order.resource,
            "submitted_tx": order.submitted_tx,
            "known": True,
        }

    # --- D3: ledger backup / restore / reconciliation -------------------------

    def backup_ledger(self) -> dict[str, _Order]:
        """A point-in-time copy of the ledger (what a backup would capture)."""
        return {oid: _Order(**vars(o)) for oid, o in self.orders.items()}

    def restore_ledger(self, backup: dict[str, _Order]) -> None:
        """Roll the ledger back to a backup - losing anything booked since."""
        self.orders = {oid: _Order(**vars(o)) for oid, o in backup.items()}

    def reconcile(self, from_block: int = 0) -> list[OnChainCredit]:
        """Find on-chain settlements to the merchant absent from the ledger.

        Returns the unreconciled credits. If ``reconciliation_enabled``, also heals
        the ledger by booking a recovered, paid order for each - what a system with
        a working reconciliation job would do automatically.
        """
        logs = self.rpc.get_logs(
            address=self.config.token_address,
            topics=[TOPIC_TRANSFER, None, topic_addr(self.config.merchant_address)],
            from_block=from_block,
        )
        known: set[SettlementIdentity | OnChainCredit] = {
            o.settlement_identity
            for o in self.orders.values()
            if o.paid and o.settlement_identity is not None
        }
        unreconciled = find_unreconciled(
            logs,
            known,
            chain_id=self.config.chain_id,
            expected_asset=self.config.token_address,
            expected_payee=self.config.merchant_address,
        )
        if self.config.reconciliation_enabled:
            for credit in unreconciled:
                identity_key = (
                    f"{credit.chain_id}:{credit.asset}:{credit.tx_hash}:{credit.log_index}"
                )
                rid = "rec_" + hashlib.sha256(identity_key.encode()).hexdigest()
                self.orders[rid] = _Order(
                    order_id=rid,
                    amount=credit.value,
                    expires_at=0,
                    quoted_fair_price=credit.value,
                    paid=True,
                    submitted_tx=credit.tx_hash,
                    resource=f"recovered::{credit.tx_hash}",
                    recovered=True,
                    settlement_identity=credit.identity,
                )
        return unreconciled

    # --- on-chain facilitator submission --------------------------------------

    def _submit_settlement(self, authorization: dict[str, Any]) -> str:
        """Safety-check, sign, and submit one local/testnet settlement transaction."""
        # This is the sole signing/submission boundary.  The policy deliberately
        # runs before calldata or transaction construction, signing, and sending.
        DEFAULT_SETTLEMENT_SAFETY_POLICY.require_safe_submission(
            rpc=self.rpc,
            configured_chain_id=self.config.chain_id,
            token_address=self.config.token_address,
            payer_address=str(authorization["from"]),
            payee_address=self.config.merchant_address,
            authorization_to=str(authorization["to"]),
            authorization_amount=authorization["value"],
            expected_amount=self.config.price,
        )
        calldata = self.token.settle_calldata(
            from_addr=str(authorization["from"]),
            to=str(authorization["to"]),
            value=int(authorization["value"]),
            valid_after=int(authorization["validAfter"]),
            valid_before=int(authorization["validBefore"]),
            nonce=str(authorization["nonce"]),
            signature=str(authorization["signature"]),
        )
        nonce = int(self.rpc.call("eth_getTransactionCount", [self.account.address, "pending"]), 16)
        try:
            gas_price = int(self.rpc.call("eth_gasPrice"), 16)
        except Exception:
            gas_price = 1_000_000_000
        tx = {
            "to": self.config.token_address,
            "data": calldata,
            "value": 0,
            "gas": self.config.gas,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": self.config.chain_id,
        }
        signed = self.account.sign_transaction(tx)
        return self.rpc.send_raw_transaction(signed.raw_transaction.to_0x_hex())


def create_app(config: SutConfig) -> Any:
    """Build the FastAPI app exposing the SUT's HTTP adapter contract."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    sut = ReferenceSut(config)
    app = FastAPI(title="psv reference SUT", version="0.1.0")

    @app.post("/quote")
    def quote() -> dict[str, Any]:
        """Expose quote creation through the reference HTTP contract."""
        return sut.quote()

    @app.post("/pay")
    def pay(body: dict[str, Any]) -> dict[str, Any]:
        """Expose authorization submission through the reference HTTP contract."""
        return sut.pay(str(body["order_id"]), dict(body["authorization"]))

    @app.get("/status/{order_id}")
    def status(order_id: str) -> dict[str, Any]:
        """Expose read-only order status through the reference HTTP contract."""
        return sut.status(order_id)

    @app.get("/resource/{order_id}")
    def resource(order_id: str) -> Any:
        """Return paid content or an HTTP 402 response for unpaid orders."""
        st = sut.status(order_id)
        if st.get("paid"):
            return JSONResponse({"resource": st["resource"]}, status_code=200)
        return JSONResponse({"error": "payment required"}, status_code=402)

    return app


def main() -> None:  # pragma: no cover - dev-machine entry point
    """Run the reference SUT on loopback from explicit environment configuration."""
    import os

    import uvicorn

    config = SutConfig(
        token_address=os.environ.get("PSV_TOKEN", "0x5FbDB2315678afecb367f032d93F642f64180aa3"),
        merchant_address=os.environ["PSV_MERCHANT"],
        facilitator_key=os.environ["PSV_FACILITATOR_KEY"],
        rpc_endpoint=os.environ.get("PSV_RPC", "http://127.0.0.1:8545"),
    )
    uvicorn.run(create_app(config), host="127.0.0.1", port=int(os.environ.get("PSV_PORT", "8402")))


if __name__ == "__main__":  # pragma: no cover
    main()
