// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import {UpgradeableMockUSDC} from "../src/UpgradeableMockUSDC.sol";

interface Vm {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
}

contract UpgradeableMockUSDCSecurityTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant PAYER_KEY = 0xA11CE;
    uint256 private constant SECP256K1N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    UpgradeableMockUSDC private token;
    bytes32 private constant NONCE = keccak256("security-regression");

    function setUp() public {
        token = new UpgradeableMockUSDC();
    }

    function _callAuthorization(address from, address to, bytes memory signature) private returns (bool) {
        (bool ok,) = address(token)
            .call(
                abi.encodeCall(token.transferWithAuthorization, (from, to, 1, 0, type(uint256).max, NONCE, signature))
            );
        return ok;
    }

    function _signature(bytes32 r, bytes32 s, uint8 v) private pure returns (bytes memory) {
        return abi.encodePacked(r, s, v);
    }

    function _digest(address from, address to, bytes32 nonce) private view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(token.TRANSFER_WITH_AUTHORIZATION_TYPEHASH(), from, to, 1, 0, type(uint256).max, nonce)
        );
        return keccak256(abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash));
    }

    function testZeroAddressBalanceCannotBeTransferredWithMalformedSignature() public {
        token.mint(address(0), 1);
        bool ok = _callAuthorization(address(0), address(this), new bytes(65));
        require(!ok, "zero-address ecrecover bypass accepted");
        require(token.balanceOf(address(0)) == 1, "zero-address balance moved");
        require(token.balanceOf(address(this)) == 0, "recipient was credited");
    }

    function testZeroRecipientIsRejected() public {
        bool ok = _callAuthorization(address(this), address(0), new bytes(65));
        require(!ok, "zero recipient accepted");
    }

    function testInvalidRecoveryIdIsRejected() public {
        bool ok =
            _callAuthorization(address(this), address(1), _signature(bytes32(uint256(1)), bytes32(uint256(1)), 29));
        require(!ok, "invalid v accepted");
    }

    function testHighSMalleableSignatureIsRejected() public {
        bool ok = _callAuthorization(
            address(this), address(1), _signature(bytes32(uint256(1)), bytes32(type(uint256).max), 27)
        );
        require(!ok, "high-s signature accepted");
    }

    function testCanonicalSignatureStillTransfers() public {
        address payer = vm.addr(PAYER_KEY);
        token.mint(payer, 1);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PAYER_KEY, _digest(payer, address(this), NONCE));

        bool ok = _callAuthorization(payer, address(this), _signature(r, s, v));
        require(ok, "canonical signature rejected");
        require(token.balanceOf(address(this)) == 1, "recipient not credited");
    }

    function testMalleableTwinOfValidSignatureIsRejected() public {
        address payer = vm.addr(PAYER_KEY);
        bytes32 nonce = keccak256("malleable-twin");
        token.mint(payer, 1);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PAYER_KEY, _digest(payer, address(this), nonce));
        bytes32 highS = bytes32(SECP256K1N - uint256(s));
        uint8 flippedV = v == 27 ? 28 : 27;

        (bool ok,) = address(token)
            .call(
                abi.encodeCall(
                    token.transferWithAuthorization,
                    (payer, address(this), 1, 0, type(uint256).max, nonce, _signature(r, highS, flippedV))
                )
            );
        require(!ok, "malleable twin accepted");
        require(token.balanceOf(payer) == 1, "payer balance moved");
    }

    function testZeroRecoveredSignerIsRejected() public {
        bool ok = _callAuthorization(address(this), address(1), _signature(bytes32(0), bytes32(uint256(1)), 27));
        require(!ok, "zero recovered signer accepted");
    }
}
