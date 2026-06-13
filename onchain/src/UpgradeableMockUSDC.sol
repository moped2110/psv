// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @title UpgradeableMockUSDC — a faithful EIP-3009 token that can change its
///        settlement event signature IN PLACE, to reproduce SC1 (ABI / event drift).
///
/// @notice Identical settlement semantics to MockUSDC (on-chain EIP-712 signature
///         verification + per-authorizer nonce tracking), but with an admin switch
///         ``eventMode`` that changes WHICH event a settlement emits:
///
///           - mode 0 (LEGACY): emits  Transfer(address,address,uint256)
///           - mode 1 (DRIFTED): emits  TransferV2(address,address,uint256,bytes32)
///
///         topic0 = keccak256 of the event signature, so the two events have
///         DIFFERENT topic0. A payment system that confirms settlement by scanning
///         for the legacy ``Transfer`` topic0 goes BLIND the moment mode flips —
///         even though funds move identically on-chain. Same contract address,
///         same storage, same balances: this is a real in-place upgrade, not a
///         redeploy, which is exactly the SC1 failure mode (a proxy upgrade that
///         silently changes the event a downstream indexer relies on).
///
/// The EIP-712 domain (name="USDC", version="2") is UNCHANGED across the flip, so
/// off-chain signatures stay valid — the drift is purely in the emitted event,
/// mirroring an upgrade that touched logs but not the signing domain.
///
/// No external imports → deploys with a single `forge create`.
/// Run Anvil with `--chain-id 84532` to match eip155:84532 off-chain signing.
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

    /// @notice Open mint for testing. Always emits legacy Transfer (minting is not
    ///         the path under test); SC1 concerns the SETTLEMENT event only.
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
        balanceOf[from] -= value;
        balanceOf[to] += value;
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
