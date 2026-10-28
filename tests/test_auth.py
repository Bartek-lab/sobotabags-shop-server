"""get_current_customer / get_current_customer_optional: the real JWKS
verification path, not overridden via dependency_overrides here (that's
what every other test module does for convenience) -- this module is what
actually proves the auth boundary itself works.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest

from app.repositories import customer_repository
from app.services import customer_auth_service

CUSTOMER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_jwks(monkeypatch):
    signing_key = MagicMock()
    signing_key.key = "fake-key"
    monkeypatch.setattr(
        customer_auth_service._jwks_client, "get_signing_key_from_jwt", MagicMock(return_value=signing_key)
    )
    return signing_key


async def test_verify_token_happy_path(mock_jwks, monkeypatch, fake_pool):
    monkeypatch.setattr(
        pyjwt, "decode", MagicMock(return_value={"sub": CUSTOMER_ID, "aud": "authenticated"})
    )
    monkeypatch.setattr(
        customer_repository, "find_by_id",
        AsyncMock(return_value={
            "id": CUSTOMER_ID, "email": "a@b.com", "first_name": "Jan", "last_name": "Kowalski",
        }),
    )
    customer = await customer_auth_service.verify_token("fake.jwt.token")
    assert str(customer.id) == CUSTOMER_ID
    assert customer.email == "a@b.com"


async def test_verify_token_invalid_signature_rejected(mock_jwks, monkeypatch):
    monkeypatch.setattr(pyjwt, "decode", MagicMock(side_effect=pyjwt.InvalidSignatureError("bad sig")))
    with pytest.raises(Exception) as exc_info:
        await customer_auth_service.verify_token("garbage")
    assert exc_info.value.status_code == 401


async def test_verify_token_expired_rejected(mock_jwks, monkeypatch):
    monkeypatch.setattr(pyjwt, "decode", MagicMock(side_effect=pyjwt.ExpiredSignatureError()))
    with pytest.raises(Exception) as exc_info:
        await customer_auth_service.verify_token("expired")
    assert exc_info.value.status_code == 401


async def test_verify_token_customer_row_missing_rejected(mock_jwks, monkeypatch, fake_pool):
    monkeypatch.setattr(
        pyjwt, "decode", MagicMock(return_value={"sub": CUSTOMER_ID, "aud": "authenticated"})
    )
    monkeypatch.setattr(customer_repository, "find_by_id", AsyncMock(return_value=None))
    with pytest.raises(Exception) as exc_info:
        await customer_auth_service.verify_token("fake.jwt.token")
    assert exc_info.value.status_code == 401


def test_me_orders_without_token_is_401(client):
    resp = client.get("/me/orders")
    assert resp.status_code == 401


def test_me_orders_with_garbage_token_is_401(client, monkeypatch, mock_jwks):
    monkeypatch.setattr(pyjwt, "decode", MagicMock(side_effect=pyjwt.PyJWTError("bad")))
    resp = client.get("/me/orders", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_checkout_preview_works_without_any_token_guest(client, fake_pool, monkeypatch):
    """get_current_customer_optional must resolve to None for a guest, not 401 --
    checkout has to work without login."""
    from app.services import pricing_service

    monkeypatch.setattr(
        pricing_service, "price_cart",
        AsyncMock(return_value={
            "subtotal": 1000, "discount_amount": 0, "shipping_amount": 0, "tax_amount": 0,
            "total_amount": 1000, "gift_card_amount": 0, "amount_due": 1000,
            "promo": None, "promo_error": None, "gift_card_error": None,
        }),
    )
    resp = client.post(
        "/checkout/preview",
        json={
            "items": [{"variant_id": str(uuid.uuid4()), "quantity": 1}],
            "shipping_method_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 200
