"""LangGraph StateGraph for the agentic payment lifecycle.

Funding -> Discovery -> Policy check -> Issuance -> Execution -> Report

The policy node is a hard gate: if it doesn't approve, the graph routes
straight to a halted end state and no StraitsX redemption/card-issuance
call is ever made.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agent.discovery import discover_product
from src.agent.state import PaymentState
from src.chain.avalanche_client import AvalancheXSGDWallet
from src.checkout.browser_checkout import run_checkout
from src.policy.spending_policy import policy
from src.straitsx.client import StraitsXClient, VirtualCard


def _log(state: PaymentState, message: str) -> None:
    state.setdefault("log", []).append(message)


def node_fund_check(state: PaymentState) -> PaymentState:
    wallet = AvalancheXSGDWallet()
    if not wallet.is_configured():
        # allow the demo to run without live chain credentials
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
    client = StraitsXClient()  # dispatches to mock/mcp/rest based on STRAITSX_MODE

    if client.mode == "mcp":
        # The sandbox flow bundles funding + issuance into one x402 payment:
        # no separate redeem_xsgd() call needed here.
        card = client.issue_virtual_card(
            amount_sgd=state["price_sgd"],
            merchant_domain=state["product_url"],
            wallet_address=state.get("wallet_address", ""),
            cardholder_name=state.get("cardholder_name", "AI Agent"),
        )
        state["card_opaque_id"] = card.card_opaque_id
        state["card_html"] = card.card_html
        state["settlement_tx"] = card.settlement_tx
        _log(
            state,
            f"Issued sandbox card {card.card_opaque_id} via x402 "
            f"(settlement tx {card.settlement_tx}).",
        )
    else:
        # Funding step in practice: transfer XSGD on-chain to the StraitsX
        # deposit address, then redeem. This demo assumes the transfer already
        # happened / uses a simulated tx hash when the wallet isn't configured.
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
        # Real sandbox/production path: no raw PAN/CVV was returned, so
        # attempt to reveal them from the one-time card_html iframe.
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
                "extraction from card_html failed -- selectors in checkout/card_reveal.py likely "
                "need updating to match the real DOM. Run `python -m src.checkout.inspect_card_html "
                "<card_html_url>` against a real card to see its structure."
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


def build_graph():
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
