"""Post-deployment smoke tests, run against a live environment:

    export SMOKE_BASE_URL=https://example.onrender.com
    export SMOKE_EXPECTED_SHA=abc1234
    pytest -m smoke smoke

Only the standard library is used, so the check needs nothing but pytest.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

pytestmark = pytest.mark.smoke

BASE_URL = os.getenv("SMOKE_BASE_URL", "").rstrip("/")
EXPECTED_SHA = os.getenv("SMOKE_EXPECTED_SHA", "")
TIMEOUT = float(os.getenv("SMOKE_TIMEOUT", "30"))


def request(path: str, payload: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers)
    # The base URL comes from the pipeline configuration, not from user input.
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:  # nosec B310
        return response.status, response.read().decode()


@pytest.fixture(scope="module", autouse=True)
def require_target():
    if not BASE_URL:
        pytest.skip("set SMOKE_BASE_URL to run smoke tests")


def test_health():
    status, body = request("/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_expected_build_is_live():
    status, body = request("/version")
    assert status == 200
    if EXPECTED_SHA:
        assert json.loads(body)["git_sha"].startswith(EXPECTED_SHA[:7])


def test_catalogue_has_products():
    status, body = request("/products?page_size=5")
    assert status == 200
    assert json.loads(body)["total"] > 0


def test_quote_prices_a_cart():
    _, body = request("/products?page_size=1&in_stock=true")
    product_id = json.loads(body)["items"][0]["id"]
    status, quote = request(
        "/orders/quote",
        {"items": [{"product_id": product_id, "quantity": 1}], "shipping_state": "MH"},
    )
    assert status == 200
    assert float(json.loads(quote)["amounts"]["total"]) > 0


def test_dashboard_renders():
    status, body = request("/")
    assert status == 200
    assert "Store dashboard" in body


def test_api_docs_are_published():
    status, body = request("/openapi.json")
    assert status == 200
    assert json.loads(body)["info"]["title"] == "ShopLite API"
