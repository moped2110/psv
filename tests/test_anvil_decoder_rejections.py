"""Rejection paths of the strict JSON-RPC decoders.

The RPC endpoint is untrusted input, so every one of these functions exists to say
*no*. The happy paths are well covered by the reconciliation and rail suites; what
was missing were the refusals themselves — and an unexercised refusal is an
assumption, not a boundary.
"""

from __future__ import annotations

import pytest

from psv.anvil import (
    _MAX_RPC_DEPTH,
    _MAX_RPC_LIST_ITEMS,
    _MAX_RPC_STRING_CHARS,
    RpcError,
    _address,
    _block_param,
    _bounded_json,
    _reject_duplicate_keys,
    _reject_json_constant,
    _validate_log,
    _validate_receipt,
)

HASH = "0x" + "11" * 32
OTHER_HASH = "0x" + "33" * 32
ADDRESS = "0x" + "22" * 20


def _log(**overrides: object) -> dict[str, object]:
    log: dict[str, object] = {
        "address": ADDRESS,
        "topics": [HASH],
        "data": "0xdeadbeef",
        "blockNumber": "0x10",
        "transactionHash": HASH,
        "transactionIndex": "0x0",
        "blockHash": OTHER_HASH,
        "logIndex": "0x0",
        "removed": False,
    }
    log.update(overrides)
    return log


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "transactionHash": HASH,
        "blockHash": OTHER_HASH,
        "blockNumber": "0x10",
        "status": "0x1",
        "logs": [_log()],
    }
    receipt.update(overrides)
    return receipt


# --- JSON decode hooks -----------------------------------------------------------


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_constants_are_rejected(constant: str) -> None:
    # NaN/Infinity are not JSON. Accepting them would let a hostile endpoint smuggle
    # a non-finite value into an amount comparison.
    with pytest.raises(ValueError, match="non-standard JSON constant"):
        _reject_json_constant(constant)


def test_duplicate_json_field_is_rejected() -> None:
    # Last-wins duplicate handling would let an endpoint show one value to a parser
    # and another to a validator.
    with pytest.raises(ValueError, match="duplicate JSON field 'status'"):
        _reject_duplicate_keys([("status", "0x1"), ("status", "0x0")])


def test_distinct_json_fields_are_kept() -> None:
    assert _reject_duplicate_keys([("a", 1), ("b", 2)]) == {"a": 1, "b": 2}


# --- Resource-exhaustion bounds --------------------------------------------------


def test_nesting_beyond_the_depth_limit_is_rejected() -> None:
    deep: object = "leaf"
    for _ in range(_MAX_RPC_DEPTH + 2):
        deep = {"next": deep}
    with pytest.raises(RpcError, match="JSON nesting exceeds"):
        _bounded_json(deep, what="result")


def test_oversized_string_is_rejected() -> None:
    with pytest.raises(RpcError, match="string exceeds size limit"):
        _bounded_json("a" * (_MAX_RPC_STRING_CHARS + 1), what="result")


def test_oversized_list_is_rejected() -> None:
    with pytest.raises(RpcError, match="list exceeds item limit"):
        _bounded_json([0] * (_MAX_RPC_LIST_ITEMS + 1), what="result")


def test_non_string_object_key_is_rejected() -> None:
    with pytest.raises(RpcError, match="object key is not a string"):
        _bounded_json({1: "value"}, what="result")


def test_unsupported_python_value_is_rejected() -> None:
    # Reachable through an injected/custom transport that returns non-JSON objects.
    with pytest.raises(RpcError, match="unsupported JSON value set"):
        _bounded_json({"result": {1, 2}}, what="result")


def test_bounded_shapes_are_accepted() -> None:
    _bounded_json({"a": [1, 2.5, None, True, "x"]}, what="result")


# --- Scalar domains --------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["0x", "0x" + "22" * 19, "0x" + "22" * 21, ADDRESS + "00", "not-an-address", 42, None],
)
def test_malformed_address_is_rejected(value: object) -> None:
    with pytest.raises(RpcError, match="expected a 20-byte address"):
        _address(value, "receipt.from")


def test_null_address_is_only_allowed_where_declared() -> None:
    # Contract creation has no `to`; everywhere else null must still fail.
    assert _address(None, "receipt.to", nullable=True) is None
    with pytest.raises(RpcError, match="expected a 20-byte address"):
        _address(None, "receipt.from")


@pytest.mark.parametrize("selector", [-1, "newest", "0x10", 1.0, True, None])
def test_invalid_block_selector_is_rejected(selector: object) -> None:
    # `True` matters: bool is an int subclass, so a sloppy check would encode it.
    with pytest.raises(ValueError, match="invalid block selector"):
        _block_param(selector)  # type: ignore[arg-type]


@pytest.mark.parametrize(("selector", "expected"), [(0, "0x0"), (16, "0x10"), ("latest", "latest")])
def test_valid_block_selectors_are_encoded(selector: int | str, expected: str) -> None:
    assert _block_param(selector) == expected


# --- Log and receipt shapes ------------------------------------------------------


@pytest.mark.parametrize("topics", ["not-a-list", [HASH] * 17])
def test_unbounded_or_malformed_topics_are_rejected(topics: object) -> None:
    with pytest.raises(RpcError, match=r"topics: expected a bounded list"):
        _validate_log(_log(topics=topics), "log")


def test_non_boolean_removed_flag_is_rejected() -> None:
    # `removed` decides whether a credit still exists after a reorg, so a truthy
    # 0/1 must not pass as a boolean.
    with pytest.raises(RpcError, match=r"removed: expected boolean"):
        _validate_log(_log(removed=0), "log")


@pytest.mark.parametrize("logs", ["not-a-list", 0])
def test_receipt_with_unbounded_logs_is_rejected(logs: object) -> None:
    with pytest.raises(RpcError, match=r"logs: expected a bounded list"):
        _validate_receipt(_receipt(logs=logs), "receipt")


def test_receipt_optional_fields_are_validated_when_present() -> None:
    with pytest.raises(RpcError, match=r"\.gasUsed: expected canonical hex quantity"):
        _validate_receipt(_receipt(gasUsed="21000"), "receipt")
    with pytest.raises(RpcError, match=r"\.from: expected a 20-byte address"):
        _validate_receipt(_receipt(**{"from": "0xnope"}), "receipt")


def test_receipt_to_may_be_null_for_contract_creation() -> None:
    validated = _validate_receipt(
        _receipt(transactionIndex="0x0", **{"from": ADDRESS, "to": None}), "receipt"
    )
    assert validated["to"] is None
