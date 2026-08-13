"""Capture the reviewed identity of an EIP-3009 token so a rail can be calibrated.

A calibrated rail in ``psv.rails`` pins a reviewed block, the runtime-code hash, and —
for proxies — the implementation slot, address and its code hash. Those values are the
reason ``rail-drift`` can tell "this is the contract we reviewed" from "something moved
underneath us". They have to come from the chain, at one block, read the same way every
time; typing them by hand from a block explorer is how a rail ends up attesting to
something nobody verified.

This tool performs that read and prints the values ready to paste. It is **read-only**:
``eth_chainId``, ``eth_getBlockByNumber``, ``eth_getCode``, ``eth_getStorageAt`` and
``eth_call`` only. It creates no transaction and holds no key.

The EIP-712 domain is *solved*, not assumed. ``FiatTokenV2_2`` does not necessarily
derive its domain separator from the current ``name()`` — a rail that copies ``name()``
into ``domain_name`` can therefore attest to a domain the contract never uses, and every
signature check built on it would be wrong in a way no test would catch. So the tool
reads ``DOMAIN_SEPARATOR()`` and reports which candidate name/version pair actually
reproduces it. If none does, it says so instead of guessing.

Usage::

    python tools/capture_rail_attestation.py \\
        --rpc https://polygon-rpc.com \\
        --token 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359 \\
        --expect-chain-id 137 \\
        --proxy-slot 0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from eth_utils import keccak

from psv.anvil import RpcClient, RpcError

_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Function selectors, derived rather than copied: a mistyped constant here would read
# the wrong slot and still produce a plausible-looking attestation.
_SEL_NAME = keccak(text="name()")[:4].hex()
_SEL_VERSION = keccak(text="version()")[:4].hex()
_SEL_DECIMALS = keccak(text="decimals()")[:4].hex()
_SEL_DOMAIN_SEPARATOR = keccak(text="DOMAIN_SEPARATOR()")[:4].hex()
_SEL_AUTHORIZATION_STATE = keccak(text="authorizationState(address,bytes32)")[:4].hex()

_EIP712_DOMAIN_TYPEHASH = keccak(
    text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)

# Names Circle's deployments have actually used for the EIP-712 domain, plus whatever
# the contract reports right now. Order matters only for readability of the output.
_NAME_CANDIDATES = ("USD Coin", "USDC", "USD Coin (PoS)", "USDC.e")
_VERSION_CANDIDATES = ("1", "2")


class CaptureError(RuntimeError):
    """A read returned something that cannot be trusted as evidence."""


@dataclass
class Capture:
    """Everything read from one token at one block."""

    chain_id: int
    block_number: int
    block_hash: str
    token_address: str
    code_sha256: str
    reported_name: str | None
    reported_version: str | None
    decimals: int
    domain_separator: str | None
    domain_solution: tuple[str, str] | None
    eip3009_state_readable: bool
    implementation_address: str | None = None
    implementation_code_sha256: str | None = None
    notes: list[str] = field(default_factory=list)


def _exact_hash(value: object, what: str) -> str:
    """Validate a 32-byte hash and normalise it to lowercase."""
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise CaptureError(f"{what} is not an exact 32-byte hash")
    return value.lower()


def _quantity(value: object, what: str) -> int:
    """Decode a canonical JSON-RPC hex quantity."""
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-fA-F]+", value) is None:
        raise CaptureError(f"{what} is not a hex quantity")
    return int(value, 16)


def _code_sha256(code: object, what: str) -> str:
    """Hash deployed runtime bytecode, refusing an empty account."""
    if not isinstance(code, str) or not code.startswith("0x") or code == "0x":
        raise CaptureError(f"{what} has no deployed runtime bytecode")
    try:
        raw = bytes.fromhex(code[2:])
    except ValueError as exc:
        raise CaptureError(f"{what} returned malformed runtime bytecode") from exc
    if not raw:
        raise CaptureError(f"{what} has no deployed runtime bytecode")
    return hashlib.sha256(raw).hexdigest()


def _decode_string(result: str) -> str | None:
    """Decode an ABI-encoded dynamic string, returning None when it is not one."""
    try:
        raw = bytes.fromhex(result[2:]) if result.startswith("0x") else bytes.fromhex(result)
    except ValueError:
        return None
    if len(raw) < 64:
        return None
    offset = int.from_bytes(raw[0:32], "big")
    if offset + 32 > len(raw):
        return None
    length = int.from_bytes(raw[offset : offset + 32], "big")
    start = offset + 32
    if length > len(raw) - start:
        return None
    try:
        return raw[start : start + length].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _try_call(rpc: RpcClient, address: str, selector: str, block: int) -> str | None:
    """Call a view function, returning None when the contract does not expose it."""
    try:
        result = rpc.eth_call(address, "0x" + selector, block)
    except Exception:  # noqa: BLE001 - a missing method is information, not a failure
        return None
    return result if isinstance(result, str) else None


def _domain_separator_for(name: str, version: str, chain_id: int, verifying: str) -> str:
    """Compute the EIP-712 domain separator for one candidate name/version pair."""
    encoded = (
        _EIP712_DOMAIN_TYPEHASH
        + keccak(text=name)
        + keccak(text=version)
        + chain_id.to_bytes(32, "big")
        + bytes(12)
        + bytes.fromhex(verifying[2:])
    )
    return "0x" + keccak(encoded).hex()


def _solve_domain(
    observed: str, chain_id: int, verifying: str, reported_name: str | None
) -> tuple[str, str] | None:
    """Find the name/version pair whose domain separator matches the observed one."""
    names = list(_NAME_CANDIDATES)
    if reported_name and reported_name not in names:
        names.insert(0, reported_name)
    for name in names:
        for version in _VERSION_CANDIDATES:
            if _domain_separator_for(name, version, chain_id, verifying) == observed.lower():
                return name, version
    return None


def _read_proxy(rpc: RpcClient, token: str, slot: str, block: int) -> tuple[str, str]:
    """Read a proxy implementation address from a storage slot and hash its code."""
    word = rpc.call("eth_getStorageAt", [token, slot, hex(block)])
    if not isinstance(word, str) or _HASH_RE.fullmatch(word) is None:
        raise CaptureError("proxy implementation slot returned a malformed word")
    if word[2:26] != "0" * 24:
        raise CaptureError("proxy implementation slot does not hold an address word")
    address = "0x" + word[-40:].lower()
    if address == "0x" + "00" * 20:
        raise CaptureError("proxy implementation slot is empty — wrong slot?")
    return address, _code_sha256(rpc.get_code(address, block), "proxy implementation")


def capture(
    *,
    rpc: RpcClient,
    token: str,
    expect_chain_id: int,
    block_tag: str,
    proxy_slot: str | None,
) -> Capture:
    """Read one token's reviewable identity at a single pinned block."""
    chain_id = rpc.chain_id()
    if chain_id != expect_chain_id:
        raise CaptureError(
            f"chain mismatch: expected {expect_chain_id}, node reports {chain_id}. "
            "Refusing to attest to a chain that was not requested."
        )
    block = rpc.get_block(block_tag)
    number = _quantity(block.get("number"), "block number")
    block_hash = _exact_hash(block.get("hash"), "block hash")

    code_sha = _code_sha256(rpc.get_code(token, number), "token")

    notes: list[str] = []
    name = _decode_string(_try_call(rpc, token, _SEL_NAME, number) or "")
    version = _decode_string(_try_call(rpc, token, _SEL_VERSION, number) or "")
    decimals_raw = _try_call(rpc, token, _SEL_DECIMALS, number)
    if decimals_raw is None:
        raise CaptureError("token does not expose decimals() — not an ERC-20 at this address")
    decimals = _quantity(decimals_raw, "decimals")

    separator_raw = _try_call(rpc, token, _SEL_DOMAIN_SEPARATOR, number)
    separator = (
        separator_raw.lower() if separator_raw and _HASH_RE.fullmatch(separator_raw) else None
    )
    solution: tuple[str, str] | None = None
    if separator is None:
        notes.append(
            "DOMAIN_SEPARATOR() is not exposed. The EIP-712 domain cannot be confirmed "
            "from the chain; do not calibrate this rail from a block explorer instead."
        )
    else:
        solution = _solve_domain(separator, chain_id, token, name)
        if solution is None:
            notes.append(
                "No candidate name/version reproduces the observed DOMAIN_SEPARATOR. "
                "The domain is something else — extend the candidate list rather than "
                "attesting to name()/version(), which would be a domain the contract "
                "does not use."
            )
        elif name is not None and solution[0] != name:
            notes.append(
                f"The EIP-712 domain name ({solution[0]!r}) differs from name() ({name!r}). "
                "This is the case the tool exists for: attest to the domain, not the label."
            )

    probe = _try_call(rpc, token, _SEL_AUTHORIZATION_STATE + "00" * 32 + "00" * 32, number)
    eip3009 = probe is not None and probe not in {"0x", ""}
    if not eip3009:
        notes.append(
            "authorizationState(address,bytes32) did not answer. This token may not "
            "implement EIP-3009; psv rails attest to that interface explicitly."
        )

    implementation_address: str | None = None
    implementation_sha: str | None = None
    if proxy_slot is not None:
        implementation_address, implementation_sha = _read_proxy(rpc, token, proxy_slot, number)

    return Capture(
        chain_id=chain_id,
        block_number=number,
        block_hash=block_hash,
        token_address=token,
        code_sha256=code_sha,
        reported_name=name,
        reported_version=version,
        decimals=decimals,
        domain_separator=separator,
        domain_solution=solution,
        eip3009_state_readable=eip3009,
        implementation_address=implementation_address,
        implementation_code_sha256=implementation_sha,
        notes=notes,
    )


def render(capture_result: Capture) -> str:
    """Render a capture as a human-readable report plus paste-ready attestation fields."""
    c = capture_result
    domain = c.domain_solution
    lines = [
        "=== observed ===",
        f"chain_id                     {c.chain_id}",
        f"block                        {c.block_number}",
        f"block_hash                   {c.block_hash}",
        f"token                        {c.token_address}",
        f"name()                       {c.reported_name!r}",
        f"version()                    {c.reported_version!r}",
        f"decimals()                   {c.decimals}",
        f"DOMAIN_SEPARATOR()           {c.domain_separator}",
        f"EIP-712 domain solved as     {domain!r}",
        f"EIP-3009 state readable      {c.eip3009_state_readable}",
        f"code sha256                  {c.code_sha256}",
        f"implementation               {c.implementation_address}",
        f"implementation code sha256   {c.implementation_code_sha256}",
    ]
    if c.notes:
        lines.append("")
        lines.append("=== read this before calibrating ===")
        lines.extend(f"- {note}" for note in c.notes)
    lines.append("")
    lines.append("=== attestation fields ===")
    if domain:
        lines.append(f"    domain_name={domain[0]!r},")
        lines.append(f"    domain_version={domain[1]!r},")
    else:
        lines.append("    domain_name=<UNRESOLVED>,")
        lines.append("    domain_version=<UNRESOLVED>,")
    lines.append(f"    decimals={c.decimals},")
    lines.append("    calibrated=True,")
    lines.append(f"    reviewed_block_number={c.block_number:_},")
    lines.append(f'    reviewed_block_hash="{c.block_hash}",')
    lines.append(f'    expected_code_sha256="{c.code_sha256}",')
    if c.implementation_address:
        lines.append(f'    implementation_address="{c.implementation_address}",')
        lines.append(f'    implementation_code_sha256="{c.implementation_code_sha256}",')
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, capture the rail identity, and print the report."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rpc", required=True, help="JSON-RPC endpoint (read-only)")
    parser.add_argument("--token", required=True, help="token contract address")
    parser.add_argument("--expect-chain-id", required=True, type=int, help="refuse other chains")
    parser.add_argument("--proxy-slot", default=None, help="implementation slot, if a proxy")
    parser.add_argument("--block-tag", default="finalized", help="block tag to pin")
    parser.add_argument("--json", action="store_true", help="emit the raw capture as JSON")
    args = parser.parse_args(argv)

    if _ADDR_RE.fullmatch(args.token) is None:
        parser.error("--token must be an exact EVM address")
    if args.proxy_slot is not None and _HASH_RE.fullmatch(args.proxy_slot) is None:
        parser.error("--proxy-slot must be an exact bytes32 slot")

    rpc = RpcClient(args.rpc)
    try:
        result = capture(
            rpc=rpc,
            token=args.token,
            expect_chain_id=args.expect_chain_id,
            block_tag=args.block_tag,
            proxy_slot=args.proxy_slot,
        )
    except CaptureError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 2
    except RpcError as exc:
        # An endpoint that refuses, redirects or answers with nonsense is an operational
        # problem, not evidence. Report it the way psv reports it everywhere else —
        # scheme and host only, no traceback, no credential in the message.
        print(f"capture failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        payload: dict[str, Any] = {
            "chain_id": result.chain_id,
            "block_number": result.block_number,
            "block_hash": result.block_hash,
            "token_address": result.token_address,
            "code_sha256": result.code_sha256,
            "reported_name": result.reported_name,
            "reported_version": result.reported_version,
            "decimals": result.decimals,
            "domain_separator": result.domain_separator,
            "domain_solution": list(result.domain_solution) if result.domain_solution else None,
            "eip3009_state_readable": result.eip3009_state_readable,
            "implementation_address": result.implementation_address,
            "implementation_code_sha256": result.implementation_code_sha256,
            "notes": result.notes,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(result))

    # An unresolved domain is not a partial success: calibrating from it would attest
    # to a domain the contract does not use.
    return 0 if result.domain_solution is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
