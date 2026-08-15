"""Signs EIP-3009 `TransferWithAuthorization` messages -- the gasless,
off-chain-signed authorization that x402's "exact" payment scheme uses to
move stablecoins without the payer needing AVAX for gas on that specific
payment.

The signer doesn't broadcast anything on-chain itself; it just produces a
signature the facilitator (StraitsX's cardapi, in this case) submits on the
payer's behalf.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

TRANSFER_WITH_AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


def build_transfer_with_authorization(
    private_key: str,
    token_name: str,
    token_version: str,
    chain_id: int,
    verifying_contract: str,
    from_address: str,
    to_address: str,
    value: int,
    valid_after: int = 0,
    valid_before: Optional[int] = None,
    nonce: Optional[bytes] = None,
) -> dict:
    """Returns the signature plus the exact authorization fields a
    facilitator needs to verify and submit the transfer.

    `value` is in the token's smallest unit (atomic units), matching what
    x402's `maxAmountRequired` field reports -- not a human-readable SGD amount.
    """
    if valid_before is None:
        valid_before = int(time.time()) + 3600
    if nonce is None:
        nonce = os.urandom(32)

    verifying_contract = Web3.to_checksum_address(verifying_contract)
    from_address = Web3.to_checksum_address(from_address)
    to_address = Web3.to_checksum_address(to_address)

    domain = {
        "name": token_name,
        "version": token_version,
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }
    message = {
        "from": from_address,
        "to": to_address,
        "value": value,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": nonce,
    }

    signable = encode_typed_data(
        domain_data=domain,
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=message,
    )
    signed = Account.sign_message(signable, private_key=private_key)

    return {
        "signature": "0x" + signed.signature.hex().removeprefix("0x"),
        "authorization": {
            "from": from_address,
            "to": to_address,
            "value": str(value),
            "validAfter": str(valid_after),
            "validBefore": str(valid_before),
            "nonce": "0x" + nonce.hex(),
        },
    }
