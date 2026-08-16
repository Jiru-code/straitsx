"""Streamlit frontend for the Agentic Payments demo.

Run with:
    streamlit run app.py

Supports two modes (selectable in the sidebar):
- **AI Agent** — LLM-driven agentic loop (requires GEMINI_API_KEY)
- **Demo Mode** — deterministic pipeline, no API key needed
"""
from __future__ import annotations

import json
import re
import time
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.graph import DEMO_SHOP_URL
from src.config import settings
from src.policy.spending_policy import policy
from src.security import seal_intent

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Agentic Payments — XSGD on Avalanche",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Cached graphs
# ---------------------------------------------------------------------------

@st.cache_resource
def get_agentic_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from src.agent.graph import build_graph
    return build_graph(checkpointer=MemorySaver())


@st.cache_resource
def get_deterministic_graph():
    from src.agent.graph import build_deterministic_graph
    return build_deterministic_graph()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "thread_id" not in st.session_state:
    st.session_state.thread_id = uuid.uuid4().hex

if "display_messages" not in st.session_state:
    st.session_state.display_messages = []

if "wallet_balance" not in st.session_state:
    st.session_state.wallet_balance = None

if "wallet_address" not in st.session_state:
    st.session_state.wallet_address = None

if "stages_completed" not in st.session_state:
    st.session_state.stages_completed = set()

if "last_tool_results" not in st.session_state:
    st.session_state.last_tool_results = []

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 💸 Agentic Payments")
    st.caption("XSGD on Avalanche C-Chain")

    st.divider()

    # Agent mode selector
    has_api_key = bool(settings.openai_api_key)
    agent_options = ["AI Agent (OpenAI)", "Demo Mode (no API key)"]
    default_idx = 0 if has_api_key else 1
    agent_mode = st.radio(
        "🤖 Agent Mode",
        agent_options,
        index=default_idx,
        help="Demo Mode runs the full pipeline without an LLM — perfect for testing.",
    )
    use_demo_mode = agent_mode == "Demo Mode (no API key)"

    if not has_api_key and not use_demo_mode:
        st.warning("⚠️ No OPENAI_API_KEY found. Switch to Demo Mode.")
        use_demo_mode = True

    st.divider()

    # StraitsX mode indicator
    mode_label = settings.straitsx_mode.upper()
    mode_color = "🟢" if settings.straitsx_mode == "mcp" else "🟡"
    st.markdown(f"**StraitsX:** {mode_color} {mode_label}")
    if settings.straitsx_mode == "mcp":
        env_label = settings.straitsx_mcp_env.capitalize()
        st.caption(f"MCP endpoint: {env_label}")

    st.divider()

    # Wallet
    st.markdown("### 🏦 Wallet")
    if st.session_state.wallet_balance is not None:
        st.metric("XSGD Balance", f"${st.session_state.wallet_balance:.2f}")
        addr = st.session_state.wallet_address or ""
        if len(addr) > 10:
            st.caption(f"`{addr[:6]}…{addr[-4:]}`")
        else:
            st.caption(f"`{addr}`")
    else:
        st.caption("Balance will appear after the agent runs.")

    st.divider()

    # Spending policy
    with st.expander("📋 Spending Policy", expanded=False):
        st.markdown(f"**Per-transaction cap:** ${policy.max_transaction_sgd:.2f} SGD")
        st.markdown(f"**Daily limit:** ${policy.daily_limit_sgd:.2f} SGD")
        st.markdown(f"**Spent today:** ${policy._spent_today:.2f} SGD")
        domains = ", ".join(policy.allowed_domains) if policy.allowed_domains else "all"
        st.markdown(f"**Allowed merchants:** {domains}")

    st.divider()

    # Lifecycle progress
    st.markdown("### 📍 Lifecycle")
    stages = [
        ("funding", "💰 Funding"),
        ("discovery", "🔍 Discovery"),
        ("policy", "🛡️ Policy Check"),
        ("issuance", "💳 Card Issuance"),
        ("checkout", "🛒 Checkout"),
    ]
    for key, label in stages:
        if key in st.session_state.stages_completed:
            st.markdown(f"✅ {label}")
        else:
            st.markdown(f"⭕ {label}")

    st.divider()

    # Reset button
    if st.button("🔄 New Session", use_container_width=True):
        st.session_state.thread_id = uuid.uuid4().hex
        st.session_state.display_messages = []
        st.session_state.wallet_balance = None
        st.session_state.wallet_address = None
        st.session_state.stages_completed = set()
        st.session_state.last_tool_results = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main chat area
# ---------------------------------------------------------------------------

st.title("💸 Agentic Payments")
if use_demo_mode:
    st.caption(
        "**Demo Mode** — running the full payment pipeline deterministically (no LLM). "
        "Type any purchase instruction to see it in action."
    )
else:
    st.caption(
        "AI-powered purchases with XSGD stablecoins, virtual cards, and autonomous checkout — "
        "all governed by a hard spending-policy gate."
    )

# Render existing messages
for msg in st.session_state.display_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_stage(tool_name: str) -> str | None:
    mapping = {
        "check_wallet_balance": "funding",
        "discover_product": "discovery",
        "evaluate_spending_policy": "policy",
        "get_spending_policy_info": "policy",
        "issue_virtual_card": "issuance",
        "execute_checkout": "checkout",
    }
    return mapping.get(tool_name)


def _update_sidebar_from_tool(tool_name: str, result_str: str) -> None:
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return
    if tool_name == "check_wallet_balance" and "balance_xsgd" in data:
        st.session_state.wallet_balance = data["balance_xsgd"]
        st.session_state.wallet_address = data.get("wallet_address", "")


def _format_tool_result(tool_name: str, result_str: str) -> str:
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str

    if tool_name == "check_wallet_balance":
        if "error" in data:
            return f"❌ {data['error']}"
        return f"💰 Balance: **{data['balance_xsgd']:.2f} XSGD** (`{data.get('wallet_address', 'N/A')}`)"

    if tool_name == "discover_product":
        if "error" in data:
            return f"❌ {data['error']}"
        return f"🔍 Found: **{data['title']}** — **{data['price_sgd']:.2f} SGD**"

    if tool_name == "evaluate_spending_policy":
        if "error" in data:
            return f"❌ {data['error']}"
        icon = "✅" if data.get("approved") else "🚫"
        return f"{icon} Policy: {data['reason']}"

    if tool_name == "issue_virtual_card":
        if data.get("issued"):
            card_info = f"💳 Card issued for **{data['amount_sgd']:.2f} SGD**"
            if data.get("pan"):
                card_info += f" — ending `{data['pan'][-4:]}`"
            if data.get("settlement_tx"):
                card_info += f"\n\n🔗 Settlement tx: `{data['settlement_tx'][:16]}…`"
            return card_info
        return f"🚫 Card not issued: {data.get('reason', data.get('error', 'unknown'))}"

    if tool_name == "execute_checkout":
        if data.get("success"):
            return f"🛒 ✅ Checkout succeeded!\n\n{data.get('detail', '')}"
        return f"🛒 ❌ Checkout failed: {data.get('detail', '')}"

    if tool_name == "get_spending_policy_info":
        return (
            f"📋 **Policy limits:**\n"
            f"- Per-transaction: {data['max_transaction_sgd']:.2f} SGD\n"
            f"- Daily limit: {data['daily_limit_sgd']:.2f} SGD\n"
            f"- Spent today: {data['spent_today_sgd']:.2f} SGD\n"
            f"- Allowed domains: {', '.join(data['allowed_merchant_domains'])}"
        )

    return f"```json\n{json.dumps(data, indent=2)}\n```"


def _extract_url(instruction: str) -> str:
    """Pull a URL out of the instruction, or fall back to the demo shop."""
    match = re.search(r"(https?://\S+|file://\S+)", instruction)
    return match.group(1) if match else DEMO_SHOP_URL


# ---------------------------------------------------------------------------
# Demo mode: deterministic pipeline rendered as chat
# ---------------------------------------------------------------------------

def _run_demo_mode(user_input: str):
    """Run the deterministic graph and render results as chat messages."""
    # SIP Layer 1: seal intent before external content enters
    seal_intent(user_input)

    product_url = _extract_url(user_input)

    graph = get_deterministic_graph()
    initial_state = {
        "instruction": user_input,
        "product_url": product_url,
        "cardholder_name": "AI Agent",
        "log": [],
    }

    with st.chat_message("assistant"):
        st.markdown(f"🚀 **Running payment pipeline** for: *{user_input}*\n")

        # Run the graph
        with st.spinner("Executing payment lifecycle…"):
            final_state = graph.invoke(initial_state)

        # Update sidebar state
        if "xsgd_balance" in final_state:
            st.session_state.wallet_balance = final_state["xsgd_balance"]
            st.session_state.wallet_address = final_state.get("wallet_address", "")

        # Render each log entry as a step
        log = final_state.get("log", [])
        stage_map = {
            "Wallet": "funding",
            "Found '": "discovery",
            "Policy:": "policy",
            "Redeemed": "issuance",
            "Issued": "issuance",
            "Checkout": "checkout",
            "Halted": None,
        }

        for entry in log:
            # Determine which stage this log entry belongs to
            for keyword, stage in stage_map.items():
                if keyword in entry:
                    if stage:
                        st.session_state.stages_completed.add(stage)
                    break

            # Pick an icon
            if "approved" in entry.lower():
                icon = "✅"
            elif "blocked" in entry.lower() or "halted" in entry.lower():
                icon = "🚫"
            elif "Wallet" in entry:
                icon = "💰"
            elif "Found" in entry:
                icon = "🔍"
            elif "Issued" in entry or "Redeemed" in entry:
                icon = "💳"
            elif "Checkout" in entry:
                icon = "🛒" if "succeeded" in entry else "❌"
            else:
                icon = "▸"

            st.markdown(f"{icon} {entry}")
            time.sleep(0.3)  # brief pause for visual effect

        # Final summary
        st.divider()
        if final_state.get("halted"):
            summary = f"🚫 **HALTED:** {final_state.get('halt_reason')}"
        else:
            pan = final_state.get("card_pan", "")
            last4 = pan[-4:] if pan else "N/A"
            summary = (
                f"### ✅ Purchase Complete\n\n"
                f"- **Item:** {final_state.get('product_title', 'N/A')}\n"
                f"- **Amount:** {final_state.get('price_sgd', 0):.2f} SGD\n"
                f"- **Card:** •••• {last4}\n"
                f"- **Result:** {final_state.get('checkout_detail', 'N/A')}"
            )
        st.markdown(summary)

    # Save to chat history
    full_text = "\n".join(f"- {entry}" for entry in log)
    if final_state.get("halted"):
        full_text += f"\n\n🚫 **HALTED:** {final_state.get('halt_reason')}"
    else:
        full_text += f"\n\n✅ **Purchase complete** — {final_state.get('product_title', '')} for {final_state.get('price_sgd', 0):.2f} SGD"
    st.session_state.display_messages.append({"role": "assistant", "content": full_text})


# ---------------------------------------------------------------------------
# Agentic mode: LLM-driven loop
# ---------------------------------------------------------------------------

def _run_agentic_mode(user_input: str):
    """Run the LLM-driven agentic graph with streaming."""
    # SIP Layer 1: seal intent before external content enters
    seal_intent(user_input)

    graph = get_agentic_graph()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    with st.chat_message("assistant"):
        response_container = st.empty()
        tool_containers: list = []
        full_response = ""

        with st.spinner("Agent is thinking..."):
            for event in graph.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                stream_mode="updates",
            ):
                for node_name, node_output in event.items():
                    if node_name == "agent":
                        msgs = node_output.get("messages", [])
                        for msg in msgs:
                            if isinstance(msg, AIMessage):
                                if msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        stage = _extract_stage(tc["name"])
                                        if stage:
                                            st.session_state.stages_completed.add(stage)
                                        status = st.status(
                                            f"🔧 Calling `{tc['name']}`…",
                                            state="running",
                                        )
                                        tool_containers.append((tc["id"], status))
                                if msg.content and not msg.tool_calls:
                                    full_response = msg.content

                    elif node_name == "tools":
                        msgs = node_output.get("messages", [])
                        for msg in msgs:
                            if isinstance(msg, ToolMessage):
                                _update_sidebar_from_tool(msg.name, msg.content)
                                for tc_id, status in tool_containers:
                                    if tc_id == msg.tool_call_id:
                                        formatted = _format_tool_result(msg.name, msg.content)
                                        status.update(
                                            label=f"✅ `{msg.name}`",
                                            state="complete",
                                        )
                                        status.markdown(formatted)
                                        break

        if full_response:
            response_container.markdown(full_response)
            st.session_state.display_messages.append(
                {"role": "assistant", "content": full_response}
            )


# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

if user_input := st.chat_input("What would you like to buy?"):
    st.session_state.display_messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    if use_demo_mode:
        _run_demo_mode(user_input)
    else:
        _run_agentic_mode(user_input)

    st.rerun()
