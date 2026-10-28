import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.repositories import order_repository

ORDER_ID = uuid.uuid4()
CUSTOMER_ID = "11111111-1111-1111-1111-111111111111"


def test_list_my_orders_requires_auth(client):
    resp = client.get("/me/orders")
    assert resp.status_code == 401


def test_list_my_orders_happy_path(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        order_repository, "list_orders_for_customer",
        AsyncMock(return_value=[{
            "id": ORDER_ID, "number": 100001, "status": "placed", "payment_status": "paid",
            "total_amount": 11500, "created_at": datetime.now(timezone.utc), "placed_at": datetime.now(timezone.utc),
            "item_count": 2,
        }]),
    )
    resp = authed_client.get("/me/orders")
    assert resp.status_code == 200
    assert resp.json()[0]["number"] == 100001


def test_get_my_order_not_found(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=None))
    resp = authed_client.get(f"/me/orders/{ORDER_ID}")
    assert resp.status_code == 404


def _order_row(**overrides):
    row = {
        "id": ORDER_ID, "number": 100001, "status": "placed", "payment_status": "paid",
        "subtotal_amount": 10000, "discount_amount": 0, "shipping_amount": 1500, "tax_amount": 0,
        "total_amount": 11500, "created_at": datetime.now(timezone.utc),
        "placed_at": datetime.now(timezone.utc), "cancelled_at": None,
        "shipping_method_name": "InPost", "pickup_point": None,
    }
    row.update(overrides)
    return row


def test_get_my_order_within_withdrawal_window_can_withdraw(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=_order_row()))
    monkeypatch.setattr(order_repository, "list_order_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(order_repository, "get_order_address", AsyncMock(return_value=None))
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))

    resp = authed_client.get(f"/me/orders/{ORDER_ID}")
    assert resp.status_code == 200
    assert resp.json()["can_withdraw"] is True


def test_get_my_order_outside_withdrawal_window_cannot_withdraw(authed_client, fake_pool, monkeypatch):
    placed_31_days_ago = datetime.now(timezone.utc) - timedelta(days=31)
    monkeypatch.setattr(
        order_repository, "get_order_for_customer",
        AsyncMock(return_value=_order_row(placed_at=placed_31_days_ago)),
    )
    monkeypatch.setattr(order_repository, "list_order_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(order_repository, "get_order_address", AsyncMock(return_value=None))
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))

    resp = authed_client.get(f"/me/orders/{ORDER_ID}")
    assert resp.json()["can_withdraw"] is False


def test_get_my_order_already_withdrawn_cannot_withdraw_again(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=_order_row()))
    monkeypatch.setattr(order_repository, "list_order_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(order_repository, "get_order_address", AsyncMock(return_value=None))
    monkeypatch.setattr(
        order_repository, "find_withdrawal",
        AsyncMock(return_value={"id": uuid.uuid4(), "status": "pending", "requested_at": datetime.now(timezone.utc)}),
    )

    resp = authed_client.get(f"/me/orders/{ORDER_ID}")
    assert resp.json()["can_withdraw"] is False
    assert resp.json()["withdrawal"] is not None


def test_get_my_order_unplaced_cannot_withdraw(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        order_repository, "get_order_for_customer",
        AsyncMock(return_value=_order_row(status="pending", placed_at=None)),
    )
    monkeypatch.setattr(order_repository, "list_order_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(order_repository, "get_order_address", AsyncMock(return_value=None))
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))

    resp = authed_client.get(f"/me/orders/{ORDER_ID}")
    assert resp.json()["can_withdraw"] is False


def test_submit_withdrawal_requires_auth(client):
    resp = client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={})
    assert resp.status_code == 401


def test_submit_withdrawal_order_not_found(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=None))
    resp = authed_client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={"reason": "zmieniłem zdanie"})
    assert resp.status_code == 404


def test_submit_withdrawal_already_submitted_is_409(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=_order_row()))
    monkeypatch.setattr(
        order_repository, "find_withdrawal",
        AsyncMock(return_value={"id": uuid.uuid4(), "status": "pending", "requested_at": datetime.now(timezone.utc)}),
    )
    resp = authed_client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={})
    assert resp.status_code == 409


def test_submit_withdrawal_outside_window_is_400(authed_client, fake_pool, monkeypatch):
    placed_31_days_ago = datetime.now(timezone.utc) - timedelta(days=31)
    monkeypatch.setattr(
        order_repository, "get_order_for_customer",
        AsyncMock(return_value=_order_row(placed_at=placed_31_days_ago)),
    )
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))
    resp = authed_client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={})
    assert resp.status_code == 400


def test_submit_withdrawal_happy_path(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=_order_row()))
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))
    withdrawal_row = {"id": uuid.uuid4(), "status": "pending", "requested_at": datetime.now(timezone.utc)}
    monkeypatch.setattr(order_repository, "insert_withdrawal", AsyncMock(return_value=withdrawal_row))
    monkeypatch.setattr(order_repository, "mark_order_withdrawn", AsyncMock(return_value=True))

    resp = authed_client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={"reason": "zmieniłem zdanie"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


def test_submit_withdrawal_race_condition_rolls_back(authed_client, fake_pool, monkeypatch):
    """Order moved out of 'placed' between the eligibility check and the
    write (race) -- must roll back and report a conflict, not silently
    insert an orphaned withdrawal row with no cancelled order behind it."""
    monkeypatch.setattr(order_repository, "get_order_for_customer", AsyncMock(return_value=_order_row()))
    monkeypatch.setattr(order_repository, "find_withdrawal", AsyncMock(return_value=None))
    monkeypatch.setattr(
        order_repository, "insert_withdrawal",
        AsyncMock(return_value={"id": uuid.uuid4(), "status": "pending", "requested_at": datetime.now(timezone.utc)}),
    )
    monkeypatch.setattr(order_repository, "mark_order_withdrawn", AsyncMock(return_value=False))

    resp = authed_client.post(f"/me/orders/{ORDER_ID}/withdrawal", json={})
    assert resp.status_code == 409
