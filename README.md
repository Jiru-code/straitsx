# Agentic Payments on XSGD / Avalanche C-Chain

A sample project for the **StraitsX hackathon track: "build the wallets, payment rails, policies
and protocols that let AI spend safely."**

It wires a **Gemini-powered LangGraph agent** through a payment lifecycle with hard safety gates:

```
Funding → Discovery → Policy check → Issuance → Execution → Report
```

The agent uses Gemini (via `langchain-google-genai`) to reason through each step, explain decisions
to the user, and handle errors intelligently — while the spending-policy gate is enforced as a
hard-coded check inside the card-issuance tool, ensuring the LLM can never bypass it.

## Demo

Launch the **Streamlit web app**:

```bash
streamlit run app.py
```

This opens a browser-based chat interface where you can:
- Type natural-language purchase instructions ("Buy me wireless earbuds from the demo shop")
- Watch the agent reason through each lifecycle step
- See wallet balance, spending policy, and stage progress in the sidebar
- Get a full transaction summary at the end

## Architecture

### Agent reasoning (LLM-driven)

The agent uses 6 LangChain tools wrapping the underlying modules:

| Tool | Purpose |
|------|---------|
| `check_wallet_balance` | Reads XSGD balance from Avalanche C-Chain |
| `discover_product` | Scrapes a product page for title + SGD price |
| `evaluate_spending_policy` | Read-only policy check (approved/blocked) |
| `get_spending_policy_info` | Returns current limits and spend tracking |
| `issue_virtual_card` | Issues a disposable card via StraitsX (**hard policy gate inside**) |
| `execute_checkout` | Fills card details on merchant page via Playwright |

### Safety model

The spending policy engine (`src/policy/spending_policy.py`) is a **deterministic gatekeeper**:
- Per-transaction cap
- Daily spending limit
- Merchant domain allow-list

It runs as a hard check inside `issue_virtual_card` — the LLM is told to call
`evaluate_spending_policy` first (so it can explain the result to the user), but even if it
didn't, the card issuance tool would refuse to proceed.

### The real sandbox flow (x402 protocol)

StraitsX's card-issuance MCP server uses the x402 payment protocol:

1. Call the `get_card_sandbox` MCP tool → returns a `cardapi` URL + payment requirements
2. `POST` that URL → HTTP 402 with an EIP-3009 challenge
3. Sign a `TransferWithAuthorization` for testnet XSGD (`src/straitsx/eip3009.py`)
4. Retry with `PAYMENT-SIGNATURE` header (`src/straitsx/x402_client.py`)
5. On success: `card_opaque_id`, `card_html`, `settlement_tx`

## Project layout

```
app.py                           Streamlit web frontend
src/
  config.py                      env/config loading
  main.py                        CLI entrypoint (agentic or --deterministic)
  agent/
    state.py                     LangGraph state schema (messages + lifecycle fields)
    tools.py                     LangChain tools wrapping all capabilities
    graph.py                     LangGraph StateGraph (agentic + deterministic)
    discovery.py                 Product page scraper (httpx + BeautifulSoup)
  chain/
    avalanche_client.py          Avalanche C-Chain XSGD wallet (web3.py)
  straitsx/
    client.py                    StraitsX API wrapper (mock/mcp/rest backends)
    mcp_client.py                MCP client for card-issuance server
    x402_client.py               x402 challenge/response protocol
    eip3009.py                   EIP-3009 TransferWithAuthorization signer
  policy/
    spending_policy.py           Spend-safety rules engine
  checkout/
    browser_checkout.py          Playwright checkout automation
    card_reveal.py               Extract PAN/CVV from card_html iframe
tests/
  fixtures/demo_shop.html        Local demo shop page
  test_spending_policy.py        Policy engine tests
  test_x402_client.py            x402 protocol tests
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # only needed for real checkout automation
cp .env.example .env          # fill in values
```

Required `.env` values:

```
GEMINI_API_KEY=...                 # Google Gemini API key for agent reasoning
AVALANCHE_RPC_URL=https://api.avax.network/ext/bc/C/rpc
WALLET_PRIVATE_KEY=0x...           # non-custodial agent wallet, testnet funds only
XSGD_CONTRACT_ADDRESS=0x...       # XSGD ERC-20 contract on C-Chain

STRAITSX_MODE=mcp                  # mock | mcp | rest
STRAITSX_MCP_SANDBOX_URL=https://card.straitsx.ai/sandbox/sse
STRAITSX_MCP_ENV=sandbox

MAX_TRANSACTION_SGD=50
DAILY_LIMIT_SGD=200
ALLOWED_MERCHANT_DOMAINS=localhost,demo-shop.test
```

## Run it

**Web demo (recommended):**
```bash
streamlit run app.py
```

**CLI:**
```bash
python -m src.main "Buy me a pair of wireless earbuds under 40 SGD from the demo shop"
```

**Legacy deterministic pipeline (no LLM):**
```bash
python -m src.main --deterministic "Buy the item at file://tests/fixtures/demo_shop.html"
```

## Safety notes

- Use a **testnet** wallet with test funds while developing. Never commit a real private key.
- The spending policy engine is the gatekeeper — it runs _inside_ `issue_virtual_card` before
  any card is created, so a runaway agent can't spend outside the configured limits.
- Sandbox card issuance runs on **Avalanche Fuji testnet (chain_id 43113)**, not mainnet.
