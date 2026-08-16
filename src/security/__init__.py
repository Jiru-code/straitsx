"""Sealed Intent Protocol (SIP) — prompt-injection defense for agentic payments.

One principle: tools are the trust boundary, not the LLM.  Every money-moving
tool validates its arguments against (a) a sealed intent contract derived from
the user's instruction and (b) a verified discovery record stored outside the
LLM's context.  The LLM's reasoning is irrelevant to security.

Three layers:
  1. SEAL   — freeze the user's intent before external content enters
  2. SANITIZE — strip injection from tool outputs before the LLM sees them
  3. ENFORCE — tools validate arguments against the sealed contract
"""
from src.security.intent_contract import get_contract, seal_intent
from src.security.enforcement import get_last_discovery

__all__ = ["seal_intent", "get_contract", "get_last_discovery"]
