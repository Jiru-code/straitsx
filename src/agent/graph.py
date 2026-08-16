"""LangGraph StateGraph for the agentic payment lifecycle.

Two graph builders are provided:

- ``build_graph()`` — the **agentic** graph: an LLM (Gemini) reasons through
  the payment lifecycle using LangChain tools.  The spending-policy gate is
  enforced inside the ``issue_virtual_card`` tool itself, so the LLM cannot
  bypass it.

- ``build_deterministic_graph()`` — the original deterministic pipeline
  (Funding → Discovery → Policy → Issuance → Checkout), kept as a fallback
  and for quick smoke-tests.
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from pathlib import Path

from src.agent.state import PaymentState
from src.agent.tools import all_tools
from src.config import settings

# Resolve the demo shop fixture path once at import time
_DEMO_SHOP = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "demo_shop.html"
DEMO_SHOP_URL = f"file://{_DEMO_SHOP}"


# ------------------------------------------------------------------
# System prompt — instructs the LLM on the payment lifecycle
# ------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
You are an AI payment agent that operates on XSGD (a Singapore Dollar \
stablecoin on the Avalanche C-Chain blockchain).  You help users purchase \
products online using disposable virtual cards issued via StraitsX.

## URLs

- If the user's message contains a URL (http://, https://, or file://), you \
  MUST use that exact URL for ``discover_product``.  Never substitute it.
- Only if the user provides NO URL at all, use this built-in demo shop: \
  {DEMO_SHOP_URL}
- Do NOT invent URLs like localhost or demo-shop.test.

## Payment lifecycle

Follow these steps **in order** for every purchase:

1. **Check wallet balance** — call ``check_wallet_balance`` to confirm XSGD \
   funds are available.
2. **Discover the product** — call ``discover_product`` with the URL from \
   the user's message (or the demo shop URL if none was given).
3. **Evaluate spending policy** — call ``evaluate_spending_policy`` with the \
   price and merchant URL.  Explain the result to the user.
4. **Issue a virtual card** — if (and only if) the policy approved, call \
   ``issue_virtual_card``.  The tool enforces the policy internally as a \
   hard gate, so do NOT skip step 3.
5. **Execute checkout** — call ``execute_checkout`` with the card details \
   and product URL to complete the purchase.

## Rules

- NEVER skip the policy check.  If the policy blocks a purchase, explain \
  why clearly and stop.
- If the wallet balance is insufficient, explain and stop.
- Narrate each step briefly so the user can follow along.
- If the user's request is ambiguous (no URL, unclear product), use the \
  demo shop URL above rather than asking.
- After checkout, give a concise summary: product, amount, card used, result.
- You can call ``get_spending_policy_info`` at any time to explain the \
  current limits to the user.
- When presenting card details, mask the full PAN — show only the last \
  4 digits (e.g. •••• 1234).
"""


# ------------------------------------------------------------------
# Agentic graph (LLM-driven)
# ------------------------------------------------------------------

def build_graph(checkpointer=None):
    """Build and compile the agentic payment graph.

    Parameters
    ----------
    checkpointer : optional
        A LangGraph checkpointer (e.g. ``MemorySaver``) for persisting
        conversation state across turns.
    """
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key or None,
    )
    llm_with_tools = llm.bind_tools(all_tools)

    def agent_node(state: PaymentState) -> dict:
        messages = list(state.get("messages", []))
        # Ensure the system prompt is always first
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    tool_node = ToolNode(all_tools)

    graph = StateGraph(PaymentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)


# ------------------------------------------------------------------
# Deterministic graph (legacy, kept for testing)
# ------------------------------------------------------------------

def build_deterministic_graph():
    """The original fixed pipeline — no LLM, no conversation.

    Funding → Discovery → Policy check → Issuance → Execution → Report
    """
    from src.agent.discovery import discover_product
    from src.chain.avalanche_client import AvalancheXSGDWallet
    from src.checkout.browser_checkout import run_checkout
    from src.policy.spending_policy import policy
    from src.straitsx.client import StraitsXClient, VirtualCard

    def _log(state: PaymentState, message: str) -> None:
        state.setdefault("log", []).append(message)

    def node_fund_check(state: PaymentState) -> PaymentState:
        wallet = AvalancheXSGDWallet()
        if not wallet.is_configured():
            _log(state, "Wallet not configured; using a simulated XSGD balance of 100.0 for the demo.")
            state["xsgd_balance"] = 100.0
            state["wallet_address"] = "0xSIMULATED"
            return state
        balance = wallet.get_xsgd_balance()
        state["xsgd_balance"] = balance
        state["wallet_address"] = wallet.address
        _log(state, f"Wallet {wallet.address} holds {balance:.2f} XSGD.")
        return state

    def node_discover(state: PaymentState) -> PaymentState:
        listing = discover_product(state["product_url"])
        state["product_title"] = listing.title
        state["price_sgd"] = listing.price_sgd
        _log(state, f"Found '{listing.title}' for {listing.price_sgd:.2f} SGD at {listing.url}.")
        return state

    def node_policy_check(state: PaymentState) -> PaymentState:
        decision = policy.evaluate(state["price_sgd"], state["product_url"])
        state["policy_approved"] = decision.approved
        state["policy_reason"] = decision.reason
        _log(state, f"Policy: {'approved' if decision.approved else 'blocked'} - {decision.reason}")
        if not decision.approved:
            state["halted"] = True
            state["halt_reason"] = decision.reason
        elif state["price_sgd"] > state.get("xsgd_balance", 0):
            state["halted"] = True
            state["halt_reason"] = (
                f"Insufficient XSGD balance: have {state.get('xsgd_balance', 0):.2f}, "
                f"need {state['price_sgd']:.2f}."
            )
            _log(state, state["halt_reason"])
        return state

    def node_issue_card(state: PaymentState) -> PaymentState:
        client = StraitsXClient()
        if client.mode == "mcp":
            card = client.issue_virtual_card(
                amount_sgd=state["price_sgd"],
                merchant_domain=state["product_url"],
                wallet_address=state.get("wallet_address", ""),
                cardholder_name=state.get("cardholder_name", "AI Agent"),
            )
            state["card_opaque_id"] = card.card_opaque_id
            state["card_html"] = card.card_html
            state["settlement_tx"] = card.settlement_tx
            _log(state, f"Issued sandbox card {card.card_opaque_id} via x402 (settlement tx {card.settlement_tx}).")
        else:
            deposit_tx_hash = "0xSIMULATED_DEPOSIT_TX"
            redemption = client.redeem_xsgd(state["price_sgd"], deposit_tx_hash)
            state["straitsx_reference"] = redemption.get("reference", "")
            _log(state, f"Redeemed {state['price_sgd']:.2f} XSGD via StraitsX (ref {state['straitsx_reference']}).")
            card = client.issue_virtual_card(state["price_sgd"], state["product_url"])
            state["card_id"] = card.card_id
            state["card_pan"] = card.pan
            state["card_expiry"] = card.expiry
            state["card_cvv"] = card.cvv
            _log(state, f"Issued virtual card {card.card_id} scoped to {card.amount_sgd:.2f} SGD.")
        policy.record_spend(state["price_sgd"])
        return state

    def node_execute_checkout(state: PaymentState) -> PaymentState:
        if state.get("card_html"):
            from src.checkout.card_reveal import reveal_card
            try:
                revealed = reveal_card(state["card_html"])
            except Exception as exc:
                state["checkout_success"] = False
                state["checkout_detail"] = f"Could not load/parse card_html: {exc}"
                _log(state, state["checkout_detail"])
                return state
            if not revealed.pan:
                state["checkout_success"] = False
                state["checkout_detail"] = (
                    f"Card issued (opaque_id={state.get('card_opaque_id')}) but automated PAN/CVV "
                    "extraction from card_html failed."
                )
                _log(state, state["checkout_detail"])
                return state
            card = VirtualCard(
                amount_sgd=state["price_sgd"],
                merchant_scope=state["product_url"],
                card_opaque_id=state.get("card_opaque_id", ""),
                pan=revealed.pan,
                expiry=revealed.expiry or "",
                cvv=revealed.cvv or "",
            )
        else:
            card = VirtualCard(
                card_id=state["card_id"],
                pan=state["card_pan"],
                expiry=state["card_expiry"],
                cvv=state["card_cvv"],
                amount_sgd=state["price_sgd"],
                merchant_scope=state["product_url"],
            )
        receipt = run_checkout(state["product_url"], card)
        state["checkout_success"] = receipt.success
        state["checkout_detail"] = receipt.detail
        _log(state, f"Checkout {'succeeded' if receipt.success else 'failed'}: {receipt.detail}")
        return state

    def node_halted(state: PaymentState) -> PaymentState:
        _log(state, f"Halted before spending: {state.get('halt_reason')}")
        return state

    def _route_after_policy(state: PaymentState) -> str:
        return "halted" if state.get("halted") else "issue_card"

    graph = StateGraph(PaymentState)
    graph.add_node("fund_check", node_fund_check)
    graph.add_node("discover", node_discover)
    graph.add_node("policy_check", node_policy_check)
    graph.add_node("issue_card", node_issue_card)
    graph.add_node("execute_checkout", node_execute_checkout)
    graph.add_node("halted", node_halted)
    graph.set_entry_point("fund_check")
    graph.add_edge("fund_check", "discover")
    graph.add_edge("discover", "policy_check")
    graph.add_conditional_edges(
        "policy_check",
        _route_after_policy,
        {"issue_card": "issue_card", "halted": "halted"},
    )
    graph.add_edge("issue_card", "execute_checkout")
    graph.add_edge("execute_checkout", END)
    graph.add_edge("halted", END)
    return graph.compile()
