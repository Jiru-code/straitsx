"""CLI entrypoint.

Usage:
    python -m src.main "Buy me wireless earbuds under 40 SGD from the demo shop"
    python -m src.main "Buy the item at file://tests/fixtures/demo_shop.html"

Runs the agentic (LLM-driven) graph by default.  Pass ``--deterministic``
to run the legacy fixed pipeline instead.
"""
from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage

from src.security import seal_intent


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m src.main "<purchase instruction>" [--deterministic]')
        sys.exit(1)

    deterministic = "--deterministic" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--deterministic"]
    instruction = " ".join(args)

    # SIP Layer 1: seal the user's intent before any external content enters
    contract = seal_intent(instruction)
    print(f"🔒 SIP: Sealed intent contract ({contract.contract_hash[:12]}…)")
    if contract.product_keywords:
        print(f"   Keywords: {', '.join(contract.product_keywords)}")
    if contract.max_price_sgd is not None:
        print(f"   Max price: {contract.max_price_sgd:.2f} SGD")

    if deterministic:
        # Legacy deterministic pipeline
        import re
        from pathlib import Path

        from src.agent.graph import build_deterministic_graph

        DEMO_SHOP = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo_shop.html"

        def _extract_url(text: str) -> str:
            match = re.search(r"(https?://\S+|file://\S+)", text)
            return match.group(1) if match else f"file://{DEMO_SHOP}"

        graph = build_deterministic_graph()
        final_state = graph.invoke({
            "instruction": instruction,
            "product_url": _extract_url(instruction),
            "cardholder_name": "AI Agent",
            "log": [],
        })

        print("\n=== Payment lifecycle log ===")
        for line in final_state.get("log", []):
            print(f"  • {line}")

        print("\n=== Result ===")
        if final_state.get("halted"):
            print(f"HALTED: {final_state.get('halt_reason')}")
        else:
            print(f"Item: {final_state.get('product_title')} ({final_state.get('price_sgd'):.2f} SGD)")
            pan = final_state.get("card_pan", "")
            print(f"Card: {final_state.get('card_id')} ending {pan[-4:]}")
            print(f"Checkout: {'success' if final_state.get('checkout_success') else 'failed'}")
            print(f"Detail: {final_state.get('checkout_detail')}")
    else:
        # Agentic (LLM-driven) graph
        from src.agent.graph import build_graph

        graph = build_graph()
        print(f"\n🤖 Agentic Payment Agent")
        print(f"   Instruction: {instruction}\n")

        final_state = graph.invoke({
            "messages": [HumanMessage(content=instruction)],
        })

        # Print the final AI response
        messages = final_state.get("messages", [])
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "ai" and msg.content and not getattr(msg, "tool_calls", None):
                print(msg.content)


if __name__ == "__main__":
    main()
