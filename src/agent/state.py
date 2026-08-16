"""Shared state passed between LangGraph nodes across the payment lifecycle.

Supports both the new LLM-driven agentic graph (which uses ``messages``)
and the legacy deterministic graph (which uses the flat fields directly).
"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class PaymentState(TypedDict, total=False):
    # ---- conversational (agentic graph) ----
    messages: Annotated[list[BaseMessage], add_messages]

    # ---- lifecycle stage (for UI progress indicator) ----
    current_stage: str  # idle | funding | discovery | policy | issuance | checkout | complete | halted

    # ---- input ----
    instruction: str          # e.g. "buy wireless earbuds under 40 SGD"
    product_url: str          # where discovery should look

    # ---- funding ----
    xsgd_balance: float
    wallet_address: str
    cardholder_name: str

    # ---- discovery ----
    product_title: str
    price_sgd: float

    # ---- policy ----
    policy_approved: bool
    policy_reason: str

    # ---- issuance -- mock/rest path ----
    straitsx_reference: str
    card_id: str
    card_pan: str
    card_expiry: str
    card_cvv: str

    # ---- issuance -- real sandbox/production MCP + x402 path ----
    card_opaque_id: str
    card_html: str
    settlement_tx: str

    # ---- execution ----
    checkout_success: bool
    checkout_detail: str

    # ---- control ----
    halted: bool
    halt_reason: Optional[str]
    log: list[str]
