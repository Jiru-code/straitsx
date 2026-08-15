"""Minimal Avalanche C-Chain client for XSGD (ERC-20) balance reads and transfers.

XSGD on Avalanche C-Chain is a standard ERC-20 token, so this only needs the
subset of the ERC-20 ABI used here: balanceOf, decimals, and transfer.
"""
from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

try:
    # web3.py 6.x
    from web3.middleware import geth_poa_middleware as poa_middleware
except ImportError:
    try:
        # web3.py 7.x renamed this
        from web3.middleware import ExtraDataToPOAMiddleware as poa_middleware
    except ImportError:
        poa_middleware = None

from src.config import settings

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


@dataclass
class TransferResult:
    tx_hash: str
    amount: float
    to_address: str


class AvalancheXSGDWallet:
    """Wraps a single non-custodial wallet's XSGD balance + transfer operations
    on Avalanche C-Chain.
    """

    def __init__(
        self,
        rpc_url: str | None = None,
        private_key: str | None = None,
        xsgd_contract_address: str | None = None,
    ) -> None:
        self.rpc_url = rpc_url or settings.avalanche_rpc_url
        self.private_key = private_key or settings.wallet_private_key
        self.xsgd_contract_address = xsgd_contract_address or settings.xsgd_contract_address

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        # Avalanche C-Chain is not strictly PoA but this middleware keeps
        # web3.py happy with some RPC providers' block formatting. Safe to
        # skip if unavailable -- standard ERC-20 reads/writes work without it.
        if poa_middleware is not None:
            try:
                self.w3.middleware_onion.inject(poa_middleware, layer=0)
            except Exception:
                pass

        if self.private_key:
            self.account = self.w3.eth.account.from_key(self.private_key)
            self.address = self.account.address
        else:
            self.account = None
            self.address = None

        self.contract = None
        if self.xsgd_contract_address:
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.xsgd_contract_address),
                abi=ERC20_ABI,
            )

    def is_configured(self) -> bool:
        return bool(self.w3.is_connected() and self.contract and self.address)

    def get_xsgd_balance(self) -> float:
        """Returns the wallet's XSGD balance as a human-readable float."""
        if not self.contract or not self.address:
            raise RuntimeError("Wallet not configured: missing XSGD contract address or private key.")
        raw = self.contract.functions.balanceOf(self.address).call()
        decimals = self.contract.functions.decimals().call()
        return raw / (10**decimals)

    def get_avax_balance(self) -> float:
        """Native AVAX balance, needed to cover gas for any transfer."""
        if not self.address:
            raise RuntimeError("Wallet not configured: missing private key.")
        raw = self.w3.eth.get_balance(self.address)
        return self.w3.from_wei(raw, "ether")

    def transfer_xsgd(self, to_address: str, amount: float) -> TransferResult:
        """Signs and broadcasts an XSGD transfer, e.g. to a StraitsX deposit
        address for redemption into fiat/card-issuance flow.
        """
        if not self.contract or not self.account:
            raise RuntimeError("Wallet not configured: missing XSGD contract address or private key.")

        decimals = self.contract.functions.decimals().call()
        raw_amount = int(amount * (10**decimals))

        tx = self.contract.functions.transfer(
            Web3.to_checksum_address(to_address), raw_amount
        ).build_transaction(
            {
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "gas": 100_000,
                "gasPrice": self.w3.eth.gas_price,
            }
        )
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return TransferResult(tx_hash=tx_hash.hex(), amount=amount, to_address=to_address)
