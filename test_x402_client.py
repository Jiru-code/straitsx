import base64
import json

import httpx

from src.straitsx.x402_client import pay_and_fetch

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff8"
TEST_WALLET = "0x3333333333333333333333333333333333333333"
TEST_PAY_TO = "0x4444444444444444444444444444444444444444"
TEST_ASSET = "0x2222222222222222222222222222222222222222"


def _make_mock_client_class(handler):
    class MockClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    return MockClient


def test_pays_402_challenge_and_returns_card(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        calls["n"] += 1
        if calls["n"] == 1:
            assert "PAYMENT-SIGNATURE" not in request.headers
            challenge = {
                "scheme": "exact",
                "network": "eip155:43113",
                "amount": "15000000",
                "payTo": TEST_PAY_TO,
                "asset": TEST_ASSET,
                "maxTimeoutSeconds": 300,
                "chainId": 43113,
                "extra": {"assetTransferMethod": "eip3009", "name": "XSGD", "version": "2"},
            }
            payment_required = base64.b64encode(json.dumps(challenge).encode()).decode()
            return httpx.Response(
                402,
                json={"x402Version": 1, "error": "PAYMENT-SIGNATURE header is required"},
                headers={"payment-required": payment_required},
            )
        assert "PAYMENT-SIGNATURE" in request.headers
        payload = json.loads(base64.b64decode(request.headers["PAYMENT-SIGNATURE"]))
        auth = payload["payload"]["authorization"]
        assert auth["value"] == "15000000"
        assert auth["to"] == TEST_PAY_TO
        assert auth["from"].lower() == TEST_WALLET.lower()
        assert payload["accepted"]["amount"] == "15000000"
        assert payload["accepted"]["payTo"] == TEST_PAY_TO
        return httpx.Response(
            200,
            json={
                "card_opaque_id": "opaque_123",
                "card_html": "https://card.straitsx.ai/view/xyz",
                "settlement_tx": "0xSETTLED",
            },
        )

    import src.straitsx.x402_client as mod

    monkeypatch.setattr(mod.httpx, "Client", _make_mock_client_class(handler))

    result = pay_and_fetch(
        cardapi_url="https://card.straitsx.ai/cardapi/abc",
        wallet_private_key=TEST_PRIVATE_KEY,
        wallet_address=TEST_WALLET,
        chain_id=43113,
    )

    assert calls["n"] == 2
    assert result["card_opaque_id"] == "opaque_123"
    assert result["settlement_tx"] == "0xSETTLED"


def test_no_challenge_returns_immediately(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"card_opaque_id": "already_paid", "card_html": "", "settlement_tx": "0xX"})

    import src.straitsx.x402_client as mod

    monkeypatch.setattr(mod.httpx, "Client", _make_mock_client_class(handler))

    result = pay_and_fetch(
        cardapi_url="https://card.straitsx.ai/cardapi/abc",
        wallet_private_key=TEST_PRIVATE_KEY,
        wallet_address=TEST_WALLET,
        chain_id=43113,
    )
    assert result["card_opaque_id"] == "already_paid"