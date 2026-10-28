import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.repositories import order_repository
from app.services import checkout_service, pricing_service, stripe_service

ORDER_ID = uuid.uuid4()
VARIANT_ID = uuid.uuid4()
SHIPPING_METHOD_ID = uuid.uuid4()


def _pricing_result(**overrides):
    result = {
        "subtotal": 10000, "discount_amount": 0, "shipping_amount": 1500, "tax_amount": 0,
        "total_amount": 11500, "gift_card_amount": 0, "amount_due": 11500,
        "promo": None, "promo_error": None, "gift_card_error": None,
    }
    result.update(overrides)
    return result


def _checkout_session_body(**overrides):
    body = {
        "items": [{"variant_id": str(VARIANT_ID), "quantity": 1}],
        "shipping_method_id": str(SHIPPING_METHOD_ID),
        "email": "guest@example.com",
        "address": {
            "first_name": "Jan", "last_name": "Kowalski", "line1": "Testowa 1",
            "city": "Warszawa", "postal_code": "00-001", "country_code": "PL",
        },
        "success_url": "https://shop.example/success",
        "cancel_url": "https://shop.example/cancel",
    }
    body.update(overrides)
    return body


# ---- /checkout/preview ----


def test_preview_empty_cart_is_400(guest_client, fake_pool):
    resp = guest_client.post(
        "/checkout/preview",
        json={"items": [], "shipping_method_id": str(SHIPPING_METHOD_ID)},
    )
    assert resp.status_code == 400


def test_preview_happy_path(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(pricing_service, "price_cart", AsyncMock(return_value=_pricing_result()))
    resp = guest_client.post(
        "/checkout/preview",
        json={"items": [{"variant_id": str(VARIANT_ID), "quantity": 1}],
              "shipping_method_id": str(SHIPPING_METHOD_ID)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount_due"] == 11500
    assert body["promo_name"] is None


def test_preview_surfaces_promo_error_without_failing_the_request(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        pricing_service, "price_cart",
        AsyncMock(return_value=_pricing_result(promo_error="kod wygasł")),
    )
    resp = guest_client.post(
        "/checkout/preview",
        json={"items": [{"variant_id": str(VARIANT_ID), "quantity": 1}],
              "shipping_method_id": str(SHIPPING_METHOD_ID), "promo_code": "EXPIRED10"},
    )
    assert resp.status_code == 200
    assert resp.json()["promo_error"] == "kod wygasł"


def test_preview_rate_limited_after_30_per_minute(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(pricing_service, "price_cart", AsyncMock(return_value=_pricing_result()))
    payload = {"items": [{"variant_id": str(VARIANT_ID), "quantity": 1}],
               "shipping_method_id": str(SHIPPING_METHOD_ID)}
    for _ in range(30):
        assert guest_client.post("/checkout/preview", json=payload).status_code == 200
    assert guest_client.post("/checkout/preview", json=payload).status_code == 429


# ---- /checkout/session ----


def test_session_empty_cart_is_400(guest_client, fake_pool):
    resp = guest_client.post(
        "/checkout/session",
        json=_checkout_session_body(items=[]),
    )
    assert resp.status_code == 400


def test_session_happy_path_redirects_to_stripe(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(checkout_service, "sweep_stale_pending_orders", AsyncMock())
    order = {
        "id": ORDER_ID, "number": 100001, "lines": [{"total_amount": 11500, "quantity": 1,
                                                       "unit_price": 11500, "product_name": "Bag",
                                                       "image_key": None}],
        "discount_amount": 0, "shipping_amount": 1500, "shipping_method_name": "InPost",
        "total_amount": 11500, "gift_card_amount": 0, "amount_due": 11500,
    }
    monkeypatch.setattr(checkout_service, "create_pending_order", AsyncMock(return_value=order))
    monkeypatch.setattr(stripe_service, "build_line_items", MagicMock(return_value=[{"fake": "line"}]))
    fake_session = MagicMock(id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123")
    monkeypatch.setattr(stripe_service, "create_checkout_session", MagicMock(return_value=fake_session))
    monkeypatch.setattr(order_repository, "update_order_metadata", AsyncMock())

    resp = guest_client.post("/checkout/session", json=_checkout_session_body())
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://checkout.stripe.com/pay/cs_test_123"


def test_session_zero_dollar_order_skips_stripe(guest_client, fake_pool, monkeypatch):
    """A gift card covering the whole order never reaches Stripe -- url is
    None and the frontend goes straight to a confirmation screen."""
    monkeypatch.setattr(checkout_service, "sweep_stale_pending_orders", AsyncMock())
    order = {
        "id": ORDER_ID, "number": 100002, "lines": [], "discount_amount": 0, "shipping_amount": 0,
        "shipping_method_name": "InPost", "total_amount": 11500, "gift_card_amount": 11500,
        "amount_due": 0,
    }
    monkeypatch.setattr(checkout_service, "create_pending_order", AsyncMock(return_value=order))
    finalize_mock = AsyncMock()
    monkeypatch.setattr(checkout_service, "finalize_zero_dollar_order", finalize_mock)
    build_line_items_mock = MagicMock()
    monkeypatch.setattr(stripe_service, "build_line_items", build_line_items_mock)

    resp = guest_client.post("/checkout/session", json=_checkout_session_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] is None
    assert body["order_number"] == 100002
    finalize_mock.assert_awaited_once()
    build_line_items_mock.assert_not_called()


def test_session_stripe_failure_cancels_the_pending_order(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(checkout_service, "sweep_stale_pending_orders", AsyncMock())
    order = {
        "id": ORDER_ID, "number": 100003, "lines": [{"total_amount": 11500, "quantity": 1,
                                                       "unit_price": 11500, "product_name": "Bag",
                                                       "image_key": None}],
        "discount_amount": 0, "shipping_amount": 1500, "shipping_method_name": "InPost",
        "total_amount": 11500, "gift_card_amount": 0, "amount_due": 11500,
    }
    monkeypatch.setattr(checkout_service, "create_pending_order", AsyncMock(return_value=order))
    monkeypatch.setattr(stripe_service, "build_line_items", MagicMock(return_value=[{"fake": "line"}]))
    monkeypatch.setattr(
        stripe_service, "create_checkout_session",
        MagicMock(side_effect=HTTPException(502, "Stripe error: down")),
    )
    cancel_mock = AsyncMock()
    monkeypatch.setattr(checkout_service, "cancel_pending_order", cancel_mock)

    resp = guest_client.post("/checkout/session", json=_checkout_session_body())
    assert resp.status_code == 502
    cancel_mock.assert_awaited_once()
    assert cancel_mock.await_args.args[1] == ORDER_ID
    assert cancel_mock.await_args.args[2] == "checkout_session_failed"


def test_session_rate_limited_after_10_per_minute(guest_client, fake_pool, monkeypatch):
    monkeypatch.setattr(checkout_service, "sweep_stale_pending_orders", AsyncMock())
    monkeypatch.setattr(checkout_service, "create_pending_order", AsyncMock(side_effect=HTTPException(400, "x")))
    payload = _checkout_session_body()
    for _ in range(10):
        resp = guest_client.post("/checkout/session", json=payload)
        assert resp.status_code == 400
    resp = guest_client.post("/checkout/session", json=payload)
    assert resp.status_code == 429


# ---- /checkout/webhook ----


def test_webhook_invalid_signature_is_400(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        stripe_service, "verify_webhook_event",
        MagicMock(side_effect=HTTPException(400, "invalid webhook signature")),
    )
    resp = client.post("/checkout/webhook", content=b"{}", headers={"stripe-signature": "bad"})
    assert resp.status_code == 400


def test_webhook_marks_order_paid_on_completed_session(client, fake_pool, monkeypatch):
    event = {
        "type": "checkout.session.completed",
        "data": {"object": MagicMock(to_dict=MagicMock(return_value={
            "client_reference_id": str(ORDER_ID), "payment_status": "paid",
        }))},
    }
    monkeypatch.setattr(stripe_service, "verify_webhook_event", MagicMock(return_value=event))
    mark_paid_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(checkout_service, "mark_order_paid", mark_paid_mock)

    resp = client.post("/checkout/webhook", content=b"{}", headers={"stripe-signature": "ok"})
    assert resp.status_code == 200
    mark_paid_mock.assert_awaited_once()
    assert mark_paid_mock.await_args.args[1] == ORDER_ID


def test_webhook_ignores_unhandled_event_types(client, fake_pool, monkeypatch):
    event = {"type": "payment_intent.created", "data": {"object": MagicMock()}}
    monkeypatch.setattr(stripe_service, "verify_webhook_event", MagicMock(return_value=event))
    mark_paid_mock = AsyncMock()
    monkeypatch.setattr(checkout_service, "mark_order_paid", mark_paid_mock)

    resp = client.post("/checkout/webhook", content=b"{}", headers={"stripe-signature": "ok"})
    assert resp.status_code == 200
    mark_paid_mock.assert_not_awaited()


def test_webhook_is_not_rate_limited(client, fake_pool, monkeypatch):
    """Stripe calls this endpoint, not a browser -- it must never be
    throttled the way the customer-facing checkout endpoints are."""
    event = {
        "type": "checkout.session.completed",
        "data": {"object": MagicMock(to_dict=MagicMock(return_value={
            "client_reference_id": str(ORDER_ID), "payment_status": "paid",
        }))},
    }
    monkeypatch.setattr(stripe_service, "verify_webhook_event", MagicMock(return_value=event))
    monkeypatch.setattr(checkout_service, "mark_order_paid", AsyncMock(return_value=True))
    for _ in range(15):
        resp = client.post("/checkout/webhook", content=b"{}", headers={"stripe-signature": "ok"})
        assert resp.status_code == 200


# ---- checkout_service.mark_order_paid idempotency (unit-level, no router) ----


async def test_mark_order_paid_is_idempotent_on_retry(monkeypatch):
    """mark_order_placed_paid's UPDATE only matches a still-pending order --
    a retried webhook call must be a harmless no-op, not re-send emails or
    re-insert a payment row."""
    import app.services.checkout_service as cs

    placed_mock = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(order_repository, "mark_order_placed_paid", placed_mock)
    monkeypatch.setattr(cs.gift_card_service, "record_payment_if_redeemed", AsyncMock())
    monkeypatch.setattr(cs, "retrieve_payment_method_details", MagicMock(return_value={
        "method_type": "card", "amount": 11500, "card_brand": "visa", "card_last4": "4242",
    }))
    monkeypatch.setattr(order_repository, "insert_payment", AsyncMock())
    monkeypatch.setattr(cs.order_email_service, "queue_order_confirmation_emails", AsyncMock())

    session = {"payment_intent": "pi_123"}
    first = await cs.mark_order_paid(object(), ORDER_ID, session)
    second = await cs.mark_order_paid(object(), ORDER_ID, session)

    assert first is True
    assert second is False
    order_repository.insert_payment.assert_awaited_once()
    cs.order_email_service.queue_order_confirmation_emails.assert_awaited_once()


# ---- GET /checkout/session/{session_id} ----


def test_checkout_status_session_not_found(client, fake_pool, monkeypatch):
    monkeypatch.setattr(order_repository, "find_order_by_checkout_session", AsyncMock(return_value=None))
    resp = client.get("/checkout/session/cs_test_unknown")
    assert resp.status_code == 404


def test_checkout_status_already_paid_in_our_db(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        order_repository, "find_order_by_checkout_session",
        AsyncMock(return_value={"id": ORDER_ID, "status": "placed", "number": 100001, "total_amount": 11500}),
    )
    resp = client.get("/checkout/session/cs_test_123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paid"
    assert body["order_number"] == 100001


def test_checkout_status_self_heals_when_stripe_says_paid_but_db_still_pending(client, fake_pool, monkeypatch):
    """The webhook is late or missing -- asking Stripe directly and marking
    the order paid here is what keeps the customer from being stuck on a
    'pending' status forever."""
    monkeypatch.setattr(
        order_repository, "find_order_by_checkout_session",
        AsyncMock(return_value={"id": ORDER_ID, "status": "pending", "number": 100001, "total_amount": 11500}),
    )
    monkeypatch.setattr(stripe_service, "retrieve_session", MagicMock(return_value={"payment_status": "paid"}))
    mark_paid_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(checkout_service, "mark_order_paid", mark_paid_mock)
    monkeypatch.setattr(
        order_repository, "get_order_summary",
        AsyncMock(return_value={"number": 100001, "total_amount": 11500}),
    )

    resp = client.get("/checkout/session/cs_test_123")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    mark_paid_mock.assert_awaited_once()


def test_checkout_status_still_genuinely_pending(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        order_repository, "find_order_by_checkout_session",
        AsyncMock(return_value={"id": ORDER_ID, "status": "pending", "number": 100001, "total_amount": 11500}),
    )
    monkeypatch.setattr(stripe_service, "retrieve_session", MagicMock(return_value={"payment_status": "unpaid"}))
    mark_paid_mock = AsyncMock()
    monkeypatch.setattr(checkout_service, "mark_order_paid", mark_paid_mock)

    resp = client.get("/checkout/session/cs_test_123")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    mark_paid_mock.assert_not_awaited()
