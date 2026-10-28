from unittest.mock import AsyncMock

from app.repositories import shipping_repository


def test_list_shipping_methods(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        shipping_repository, "list_active_shipping_methods",
        AsyncMock(return_value=[{
            "id": "11111111-1111-1111-1111-111111111111", "code": "inpost_locker",
            "name": "Paczkomat InPost", "price_amount": 1200, "carrier_code": "inpost",
            "requires_pickup_point": True, "free_above_amount": None,
        }]),
    )
    resp = client.get("/shipping-methods")
    assert resp.status_code == 200
    assert resp.json()[0]["code"] == "inpost_locker"


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401


def test_me_happy_path(authed_client):
    resp = authed_client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "customer@example.com"
