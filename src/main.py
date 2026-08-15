"""CLI entrypoint.

Usage:
    python -m src.main "Buy me wireless earbuds under 40 SGD from the demo shop"
    python -m src.main "Buy the item at file://tests/fixtures/demo_shop.html"

If the instruction doesn't contain a URL, this falls back to the bundled
demo shop fixture so the pipeline is runnable with zero setup.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from src.agent.graph import build_graph

DEMO_SHOP = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo_shop.html"


def _extract_url(instruction: str) -> str:
    match = re.search(r"(https?://\S+|file://\S+)", instruction)
    if match:
        return match.group(1)
    return f"file://{DEMO_SHOP}"


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m src.main "<purchase instruction>"')
        sys.exit(1)

    instruction = " ".join(sys.argv[1:])
    product_url = _extract_url(instruction)

    graph = build_graph()
    initial_state = {
        "instruction": instruction,
        "product_url": product_url,
        "cardholder_name": "AI Agent",
        "log": [],
    }
    final_state = graph.invoke(initial_state)

    print("\n=== Payment lifecycle log ===")
    for line in final_state.get("log", []):
        print(f"- {line}")

    print("\n=== Result ===")
    if final_state.get("halted"):
        print(f"HALTED: {final_state.get('halt_reason')}")
    else:
        print(f"Item: {final_state.get('product_title')} ({final_state.get('price_sgd'):.2f} SGD)")
        print(f"Card: {final_state.get('card_id')} ending {final_state.get('card_pan', '')[-4:]}")
        print(f"Checkout: {'success' if final_state.get('checkout_success') else 'failed'}")
        print(f"Detail: {final_state.get('checkout_detail')}")


if __name__ == "__main__":
    main()
