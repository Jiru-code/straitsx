"""Central place to load and validate environment configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    val = os.getenv(name, default)
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # LLM
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    # Avalanche C-Chain
    avalanche_rpc_url: str = field(
        default_factory=lambda: os.getenv("AVALANCHE_RPC_TEST_URL", "https://api.avax.network/ext/bc/C/rpc")
    )
    # 43114 = C-Chain mainnet (required for the hackathon submission).
    # 43113 = Fuji testnet (required by the sandbox card-issuance MCP tool).
    # These must match whichever network your RPC URL and wallet funds are on.
    avalanche_chain_id: int = field(default_factory=lambda: int(os.getenv("AVALANCHE_CHAIN_ID", "43114")))
    wallet_private_key: str = field(default_factory=lambda: os.getenv("WALLET_PRIVATE_KEY", ""))
    xsgd_contract_address: str = field(default_factory=lambda: os.getenv("XSGD_CONTRACT_ADDRESS", ""))

    # StraitsX REST (legacy path, requires a StraitsX business account)
    straitsx_api_base: str = field(default_factory=lambda: os.getenv("STRAITSX_API_BASE", "https://api.straitsx.com"))
    straitsx_api_key: str = field(default_factory=lambda: os.getenv("STRAITSX_API_KEY", ""))
    straitsx_api_secret: str = field(default_factory=lambda: os.getenv("STRAITSX_API_SECRET", ""))

    # StraitsX card-issuance MCP server (no business account needed for sandbox)
    straitsx_mcp_sandbox_url: str = field(
        default_factory=lambda: os.getenv("STRAITSX_MCP_SANDBOX_URL", "https://card.straitsx.ai/sandbox/sse")
    )
    straitsx_mcp_production_url: str = field(
        default_factory=lambda: os.getenv("STRAITSX_MCP_PRODUCTION_URL", "https://card.straitsx.ai/production/sse")
    )
    straitsx_mcp_env: str = field(default_factory=lambda: os.getenv("STRAITSX_MCP_ENV", "sandbox"))

    # "mock" | "mcp" | "rest" -- which backend StraitsXClient talks to
    straitsx_mode: str = field(default_factory=lambda: os.getenv("STRAITSX_MODE", "mock"))

    @property
    def straitsx_mcp_url(self) -> str:
        return self.straitsx_mcp_production_url if self.straitsx_mcp_env == "production" else self.straitsx_mcp_sandbox_url

    # Spending policy
    max_transaction_sgd: float = field(default_factory=lambda: float(os.getenv("MAX_TRANSACTION_SGD", "50")))
    daily_limit_sgd: float = field(default_factory=lambda: float(os.getenv("DAILY_LIMIT_SGD", "200")))
    allowed_merchant_domains: list[str] = field(
        default_factory=lambda: _list("ALLOWED_MERCHANT_DOMAINS", "localhost,demo-shop.test")
    )

    # Checkout
    checkout_headless: bool = field(default_factory=lambda: _bool("CHECKOUT_HEADLESS", True))

    # Sealed Intent Protocol (SIP)
    sip_enabled: bool = field(default_factory=lambda: _bool("SIP_ENABLED", True))
    sip_keyword_threshold: float = field(
        default_factory=lambda: float(os.getenv("SIP_KEYWORD_THRESHOLD", "0.3"))
    )
    sip_consistency_threshold: float = field(
        default_factory=lambda: float(os.getenv("SIP_CONSISTENCY_THRESHOLD", "0.5"))
    )
    sip_max_title_length: int = field(
        default_factory=lambda: int(os.getenv("SIP_MAX_TITLE_LENGTH", "200"))
    )
    sip_block_on_injection: bool = field(
        default_factory=lambda: _bool("SIP_BLOCK_ON_INJECTION", True)
    )


settings = Settings()
