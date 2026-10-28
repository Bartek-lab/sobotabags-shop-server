"""Unit-level tests for checkout_service business rules that aren't already
exercised through the router in test_checkout.py: stale-order sweeping,
cancellation side effects, and create_pending_order's validation rules.
conn is a plain object() in every call here -- checkout_service never
queries it directly, only passes it through to already-mocked repository
calls.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.repositories import order_repository, pricing_repository
from app.schemas.checkout import AddressIn, CheckoutItemIn, CheckoutSessionIn, PickupPointIn
from app.services import checkout_service as cs
from app.services import gift_card_service, pricing_service

ORDER_ID = uuid.uuid4()
VARIANT_ID = uuid.uuid4()
METHOD_ID = uuid.uuid4()


def _session_in(**overrides):
    body = dict(
        items=[CheckoutItemIn(variant_id=VARIANT_ID, quantity=1)],
        shipping_method_id=METHOD_ID,
        email="guest@example.com",
        address=AddressIn(first_name="Jan", last_name="Kowalski", line1="Testowa 1",
                           city="Warszawa", postal_code="00-001", country_code="PL"),
        success_url="https://shop.example/success",
        cancel_url="https://shop.example/cancel",
    )
    body.update(overrides)
    return CheckoutSessionIn(**body)


async def test_sweep_stale_pending_orders_cancels_each_one(monkeypatch):
    monkeypatch.setattr(
        order_repository, "find_stale_pending_order_ids",
        AsyncMock(return_value=[ORDER_ID, uuid.uuid4()]),
    )
    cancel_mock = AsyncMock()
    monkeypatch.setattr(cs, "cancel_pending_order", cancel_mock)
    await cs.sweep_stale_pending_orders(object())
    assert cancel_mock.await_count == 2
    assert cancel_mock.await_args_list[0].args[2] == "expired"


async def test_sweep_stale_pending_orders_noop_when_none_stale(monkeypatch):
    monkeypatch.setattr(order_repository, "find_stale_pending_order_ids", AsyncMock(return_value=[]))
    cancel_mock = AsyncMock()
    monkeypatch.setattr(cs, "cancel_pending_order", cancel_mock)
    await cs.sweep_stale_pending_orders(object())
    cancel_mock.assert_not_awaited()


async def test_cancel_pending_order_noop_if_already_not_pending(monkeypatch):
    """mark_order_cancelled's UPDATE only matches a still-pending order --
    if it returns False, nothing else (gift card refund, restock) should
    run at all."""
    monkeypatch.setattr(order_repository, "mark_order_cancelled", AsyncMock(return_value=False))
    refund_mock = AsyncMock()
    monkeypatch.setattr(gift_card_service, "refund", refund_mock)
    await cs.cancel_pending_order(object(), ORDER_ID, "expired")
    refund_mock.assert_not_awaited()


async def test_cancel_pending_order_refunds_gift_card_and_restocks_reserved_lines(monkeypatch):
    monkeypatch.setattr(order_repository, "mark_order_cancelled", AsyncMock(return_value=True))
    refund_mock = AsyncMock()
    monkeypatch.setattr(gift_card_service, "refund", refund_mock)
    monkeypatch.setattr(
        order_repository, "get_reserved_order_lines",
        AsyncMock(return_value=[{"variant_id": VARIANT_ID, "quantity": 2}]),
    )
    increment_mock = AsyncMock()
    monkeypatch.setattr(pricing_repository, "increment_variant_stock", increment_mock)

    await cs.cancel_pending_order(object(), ORDER_ID, "expired")

    refund_mock.assert_awaited_once()
    increment_mock.assert_awaited_once()
    assert increment_mock.await_args.args[1] == VARIANT_ID
    assert increment_mock.await_args.args[2] == 2


async def test_create_pending_order_requires_email_for_guest(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        await cs.create_pending_order(object(), _session_in(email=None), None)
    assert exc_info.value.status_code == 400


async def test_create_pending_order_unknown_shipping_method_is_400(monkeypatch):
    monkeypatch.setattr(pricing_repository, "get_active_shipping_method", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc_info:
        await cs.create_pending_order(object(), _session_in(), None)
    assert exc_info.value.status_code == 400


async def test_create_pending_order_pickup_point_required_but_missing_is_400(monkeypatch):
    monkeypatch.setattr(
        pricing_repository, "get_active_shipping_method",
        AsyncMock(return_value={"id": METHOD_ID, "code": "inpost_locker", "name": "Paczkomat",
                                 "carrier_code": "inpost", "requires_pickup_point": True}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await cs.create_pending_order(object(), _session_in(address=None, pickup_point=None), None)
    assert exc_info.value.status_code == 400


async def test_create_pending_order_address_required_but_missing_is_400(monkeypatch):
    monkeypatch.setattr(
        pricing_repository, "get_active_shipping_method",
        AsyncMock(return_value={"id": METHOD_ID, "code": "poczta", "name": "Poczta Polska",
                                 "carrier_code": "poczta_polska", "requires_pickup_point": False}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await cs.create_pending_order(object(), _session_in(address=None), None)
    assert exc_info.value.status_code == 400


async def test_create_pending_order_happy_path_with_pickup_point(monkeypatch):
    monkeypatch.setattr(
        pricing_repository, "get_active_shipping_method",
        AsyncMock(return_value={"id": METHOD_ID, "code": "inpost_locker", "name": "Paczkomat",
                                 "carrier_code": "inpost", "requires_pickup_point": True}),
    )
    pricing_result = {
        "lines": [{"total_amount": 10000, "quantity": 1, "unit_price": 10000,
                   "product_name": "Bag", "image_key": None}],
        "subtotal": 10000, "discount_amount": 0, "shipping_amount": 1200, "tax_amount": 0,
        "total_amount": 11200, "shipping_method": {"id": METHOD_ID, "code": "inpost_locker",
                                                     "name": "Paczkomat", "carrier_code": "inpost"},
        "promo": None, "gift_card": None, "gift_card_amount": 0, "amount_due": 11200,
    }
    monkeypatch.setattr(pricing_service, "price_cart", AsyncMock(return_value=pricing_result))
    monkeypatch.setattr(
        order_repository, "insert_order",
        AsyncMock(return_value={"id": ORDER_ID, "number": 100005}),
    )
    insert_line_mock = AsyncMock()
    monkeypatch.setattr(order_repository, "insert_order_line", insert_line_mock)
    insert_address_mock = AsyncMock()
    monkeypatch.setattr(order_repository, "insert_order_address", insert_address_mock)

    body = _session_in(
        address=None,
        pickup_point=PickupPointIn(point_name="WAW01A", first_name="Jan", last_name="Kowalski"),
    )
    result = await cs.create_pending_order(object(), body, None)

    assert result["id"] == ORDER_ID
    assert result["amount_due"] == 11200
    insert_line_mock.assert_awaited_once()
    insert_address_mock.assert_not_awaited()  # pickup point, not a shipping address


async def test_create_pending_order_empty_priced_lines_is_400(monkeypatch):
    """Every requested variant turned out to be unsellable (e.g. deactivated
    between preview and checkout) -- pricing_service returns no lines, and
    there's nothing meaningful left to charge for."""
    monkeypatch.setattr(
        pricing_repository, "get_active_shipping_method",
        AsyncMock(return_value={"id": METHOD_ID, "code": "poczta", "name": "Poczta Polska",
                                 "carrier_code": "poczta_polska", "requires_pickup_point": False}),
    )
    monkeypatch.setattr(
        pricing_service, "price_cart",
        AsyncMock(return_value={"lines": [], "subtotal": 0, "discount_amount": 0, "shipping_amount": 0,
                                 "tax_amount": 0, "total_amount": 0, "shipping_method": {"id": METHOD_ID,
                                 "code": "poczta", "name": "Poczta Polska"}, "promo": None,
                                 "gift_card": None, "gift_card_amount": 0, "amount_due": 0}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await cs.create_pending_order(object(), _session_in(), None)
    assert exc_info.value.status_code == 400
