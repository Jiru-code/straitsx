# Agentic Payments on XSGD / Avalanche C-Chain

A sample project for the **StraitsX hackathon track: "build the wallets, payment rails, policies
and protocols that let AI spend safely."**

It wires a LangGraph agent through four stages that mirror a payment lifecycle:

```
Funding -> Discovery -> Policy check -> Issuance -> Execution -> Report
```

- **Funding** — reads an XSGD (ERC-20) balance from a non-custodial wallet on Avalanche C-Chain.
- **Discovery** — an LLM-driven agent parses a purchase instruction and scans a product page
  for its price.
- **Policy** — a spending-policy engine approves or blocks the purchase _before_ any money moves
  (per-transaction cap, daily cap, merchant allow-list). This is the "spend safely" core of the
  track.
- **Issuance** — redeems XSGD via the StraitsX API and requests a disposable virtual card scoped
  to the purchase amount.
- **Execution** — uses the virtual card to complete checkout.

## The real sandbox flow (confirmed)

StraitsX's card-issuance MCP server doesn't hand back a ready-to-use card in one call. The
actual flow, confirmed against the real tool schemas:

1. Call the `get_card_sandbox` MCP tool with `wallet_address`, `cardholder_name`, `amount_sgd`
   (5-30 SGD). It does **not** return a card — it returns a `cardapi` URL and x402 payment
   requirements.
2. `POST` that `cardapi` URL directly (plain HTTP, not MCP — GET returns 405 Method Not
   Allowed, confirmed against the live endpoint). It responds **HTTP 402 Payment Required**
   with the price, pay-to address, asset contract, and EIP-712 domain info.
3. Sign an **EIP-3009 `TransferWithAuthorization`** for that amount of testnet XSGD — a gasless,
   off-chain signature (`src/straitsx/eip3009.py`).
4. Retry the request with a `PAYMENT-SIGNATURE` header carrying that signed authorization
   (`src/straitsx/x402_client.py`).
5. On success: `card_opaque_id`, `card_html` (a one-time iframe, **not** raw PAN/CVV), and
   `settlement_tx`.

This is genuinely the x402 protocol at work — see `src/straitsx/x402_client.py` for the
challenge/sign/retry implementation, tested against a mocked 402 server in
`tests/test_x402_client.py`-style verification during development.

**Important:** sandbox card issuance runs on **Avalanche Fuji testnet (chain_id 43113)**, not
C-Chain mainnet (43114). Your hackathon track requires mainnet XSGD for the actual submission —
`AVALANCHE_CHAIN_ID` in `.env` controls which network signing/reads target, and it must match
whatever `AVALANCHE_RPC_URL` and your wallet funds are actually on. Expect to run dev/testing on
Fuji via the sandbox MCP endpoint, then switch both the chain config and
`STRAITSX_MCP_ENV=production` for the real submission.

**Card details aren't returned directly.** Since sandbox gives you `card_html` (an iframe) rather
than a PAN/expiry/CVV, `src/checkout/card_reveal.py` uses Playwright to load that iframe and
extract card fields via CSS selectors. Those selectors are **best guesses** — this was built
without network access to `card.straitsx.ai` to inspect the real DOM. Once you have a real
sandbox card, run:

```bash
python -m src.checkout.inspect_card_html "<card_html_url>"
```

and update `SELECTORS` in `card_reveal.py` to match what's actually there. If checkout fails with
a "selectors likely need updating" message, this is why.

## Why things are mocked where they are

Two integrations need credentials/access this repo can't ship with:

1. **StraitsX card issuance / redemption.** `src/straitsx/client.py` supports three backends,
   selected by `STRAITSX_MODE` in `.env`:
   - **`mock`** (default) — synthetic responses, no network needed. Runs the whole graph
     end-to-end with zero setup, using a simplified single-call issuance path (skips the x402
     dance) so you can sanity-check the rest of the pipeline fast.
   - **`mcp`** — the real flow described above, against StraitsX's card-issuance MCP server:
     - sandbox: `https://card.straitsx.ai/sandbox/sse`
     - production: `https://card.straitsx.ai/production/sse`

     Recommended if you don't have a StraitsX business account — the sandbox MCP server handles
     StraitsX-side auth for you. Set `STRAITSX_MODE=mcp` and `STRAITSX_MCP_ENV=sandbox`.

     Tool names `get_card_sandbox` and `view_card_sandbox` are hardcoded per the confirmed
     schema; if the server ever renames them, `find_tool()` in `mcp_client.py` is the fallback.
     Run `python -m src.straitsx.list_mcp_tools` any time to see the live tool list.

   - **`rest`** — the raw StraitsX REST API (https://docs.straitsx.com/), gated behind a
     business account. Endpoint paths in `client.py` are marked `TODO`.

2. **Checkout automation** — `src/checkout/browser_checkout.py` uses Playwright against
   whatever URL discovery finds, and (in `mcp` mode) `card_reveal.py` to pull card fields out of
   the iframe first. Both run against a local demo HTML page (`tests/fixtures/demo_shop.html`)
   by default.

Everything else — the Avalanche C-Chain read/write, the LangGraph orchestration, the spending
policy engine, and the EIP-3009/x402 signing logic — is real, not mocked, and was verified
against a simulated 402 server during development (see the conversation this repo came from for
the test transcript).

## Project layout

```
src/
  config.py                 env/config loading
  chain/avalanche_client.py Avalanche C-Chain XSGD wallet (web3.py)
  straitsx/client.py        StraitsX API wrapper (redeem + card issuance), mockable
  policy/spending_policy.py Spend-safety rules engine
  agent/state.py            LangGraph state schema
  agent/tools.py            LangChain tools wrapping the above
  agent/graph.py            LangGraph StateGraph wiring the lifecycle
  checkout/browser_checkout.py Playwright checkout automation
  main.py                   CLI entrypoint
tests/fixtures/demo_shop.html  local page to test discovery + checkout against
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
ANTHROPIC_API_KEY=...              # or OPENAI_API_KEY, see config.py
AVALANCHE_RPC_URL=https://api.avax.network/ext/bc/C/rpc
WALLET_PRIVATE_KEY=0x...           # non-custodial agent wallet, testnet funds only
XSGD_CONTRACT_ADDRESS=0x...        # XSGD ERC-20 contract on C-Chain, see StraitsX docs

STRAITSX_MODE=mock                 # mock | mcp | rest
STRAITSX_MCP_SANDBOX_URL=https://card.straitsx.ai/sandbox/sse
STRAITSX_MCP_PRODUCTION_URL=https://card.straitsx.ai/production/sse
STRAITSX_MCP_ENV=sandbox
STRAITSX_API_KEY=...               # only needed for STRAITSX_MODE=rest
STRAITSX_API_SECRET=...            # only needed for STRAITSX_MODE=rest

MAX_TRANSACTION_SGD=50
DAILY_LIMIT_SGD=200
ALLOWED_MERCHANT_DOMAINS=localhost,demo-shop.test
```

## Run it

```bash
python -m src.main "Buy me a pair of wireless earbuds under 40 SGD from the demo shop"
```

This runs the full graph and prints each stage's output, including the policy engine's decision
and (in mock mode) a synthetic virtual card and checkout receipt.

## Extending to real StraitsX endpoints

`src/straitsx/client.py` isolates every HTTP call behind three methods:
`get_xsgd_balance()`, `redeem_xsgd(amount)`, `issue_virtual_card(amount, merchant)`. Swap the
mock branches for real requests once you have sandbox credentials and the exact endpoint paths
from the StraitsX API docs (https://docs.straitsx.com/) — the rest of the graph doesn't need to
change.

## Safety notes

- Use a **testnet** wallet with test funds while developing. Never commit a real private key.
- The spending policy engine is intentionally the gatekeeper node in the graph — it runs _before_
  `redeem_xsgd` or `issue_virtual_card` are ever called, so a runaway agent can't spend outside
  the configured limits regardless of what the LLM decides.
