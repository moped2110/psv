// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @title UpgradeableMockUSDC — a faithful EIP-3009 token with configurable quirks
///        for system-level payment verification.
///
/// @notice Same settlement semantics as a real EIP-3009 token (on-chain EIP-712
///         signature verification + per-authorizer nonce tracking), plus two admin
///         switches the harness uses to reproduce damage cases:
///           - ``eventMode``: 0 = legacy Transfer(address,address,uint256),
///             1 = drifted TransferV2(address,address,uint256,bytes32). Different
///             topic0 → an event-watching indexer goes blind (SC1, ABI drift).
///           - ``feeBps``: fee-on-transfer in basis points. The recipient NETS
///             value*(1 - feeBps/10000) while the Transfer event still reports the
///             GROSS value — the trap that fools amount-from-event checks (T-class).
///
/// The EIP-712 domain (name="USDC", version="2", chainId=block.chainid) binds the
/// signature to the chain, so an authorization signed for another chain reverts
/// here (C0, cross-chain replay defense).
///
/// No external imports → deploys with a single `forge create`. Run Anvil with
/// `--chain-id 84532` to match eip155:84532 off-chain signing.
contract UpgradeableMockUSDC {
    string public constant name = "USDC";
    string public constant version = "2";
    string public constant symbol = "USDC";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    /// @notice 0 = legacy Transfer event, 1 = drifted TransferV2 event.
    uint8 public eventMode;
    /// @notice Fee-on-transfer in basis points (0 = none).
    uint16 public feeBps;
    address public admin;

    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );
    bytes32 public constant EIP712_DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    // Legacy settlement event — what a v1 indexer subscribes to.
    event Transfer(address indexed from, address indexed to, uint256 value);
    // Drifted settlement event after the "upgrade" — different signature => different topic0.
    event TransferV2(address indexed from, address indexed to, uint256 value, bytes32 ref);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);
    event EventModeChanged(uint8 previousMode, uint8 newMode);
    event FeeBpsChanged(uint16 previousBps, uint16 newBps);

    constructor() {
        admin = msg.sender;
    }

    /// @notice Flip the settlement event signature in place. The "ABI drift".
    function setEventMode(uint8 mode) external {
        require(msg.sender == admin, "only admin");
        require(mode <= 1, "unknown mode");
        emit EventModeChanged(eventMode, mode);
        eventMode = mode;
    }

    /// @notice Turn on fee-on-transfer (basis points). Capped at 10% for tests.
    function setFeeBps(uint16 bps) external {
        require(msg.sender == admin, "only admin");
        require(bps <= 1000, "fee too high");
        emit FeeBpsChanged(feeBps, bps);
        feeBps = bps;
    }

    /// @notice Open mint for testing. Always emits legacy Transfer (minting is not
    ///         the path under test).
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
        emit Transfer(address(0), to, amount);
    }

    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                EIP712_DOMAIN_TYPEHASH,
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                block.chainid,
                address(this)
            )
        );
    }

    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        bytes calldata signature
    ) external {
        require(block.timestamp > validAfter, "auth: not yet valid");
        require(block.timestamp < validBefore, "auth: expired");
        require(!authorizationState[from][nonce], "auth: nonce already used");

        bytes32 structHash = keccak256(
            abi.encode(
                TRANSFER_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR(), structHash));
        require(_recover(digest, signature) == from, "auth: invalid signature");

        authorizationState[from][nonce] = true; // mark before transfer (replay safety)
        require(balanceOf[from] >= value, "insufficient balance");

        // Fee-on-transfer: the payer is debited `value`, but the recipient nets
        // `value - fee` (the fee is parked with admin). The Transfer event below
        // still reports the GROSS `value` — the trap that fools event-amount checks.
        uint256 fee = (value * feeBps) / 10000;
        balanceOf[from] -= value;
        balanceOf[to] += value - fee;
        if (fee > 0) {
            balanceOf[admin] += fee;
        }
        emit AuthorizationUsed(from, nonce);

        // The drift: identical fund movement, different settlement event.
        if (eventMode == 0) {
            emit Transfer(from, to, value);
        } else {
            emit TransferV2(from, to, value, nonce);
        }
    }

    function _recover(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "bad sig length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (v < 27) {
            v += 27;
        }
        return ecrecover(digest, v, r, s);
    }
}
