"""Checkout endpoints. Routing only -- request parsing and response shaping;
all pricing/order-lifecycle/Stripe logic lives in app/services/.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import get_current_customer_optional
from app.db import pool
from app.repositories import order_repository
from app.schemas.checkout import (
    CheckoutSessionIn,
    CheckoutSessionOut,
    CheckoutStatusOut,
    PricingPreviewIn,
    PricingPreviewOut,
)
from app.schemas.customer import CurrentCustomer
from app.services import checkout_service, pricing_service, stripe_service

router = APIRouter(tags=["checkout"])


@router.post("/checkout/preview", response_model=PricingPreviewOut)
async def preview_checkout(
    body: PricingPreviewIn, customer: CurrentCustomer | None = Depends(get_current_customer_optional)
):
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")
    async with pool().acquire() as conn:
        pricing = await pricing_service.price_cart(
            conn, body.items, body.shipping_method_id, body.promo_code, body.gift_card_code,
            customer, customer.email if customer else None, lock=False, strict=False,
        )
    return PricingPreviewOut(
        subtotal_amount=pricing["subtotal"], discount_amount=pricing["discount_amount"],
        shipping_amount=pricing["shipping_amount"], tax_amount=pricing["tax_amount"],
        total_amount=pricing["total_amount"], gift_card_amount=pricing["gift_card_amount"],
        amount_due=pricing["amount_due"],
        promo_name=pricing["promo"]["name"] if pricing["promo"] else None,
        promo_error=pricing["promo_error"], gift_card_error=pricing["gift_card_error"],
    )


@router.post("/checkout/session", response_model=CheckoutSessionOut)
async def create_checkout_session(
    body: CheckoutSessionIn,
    customer: CurrentCustomer | None = Depends(get_current_customer_optional),
):
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")

    # Deliberately its own transaction, committed before order creation even
    # starts: if the order below fails validation and rolls back, that must
    # not take this cleanup down with it -- a bad request on one checkout
    # attempt shouldn't silently un-sweep stale orders from unrelated ones.
    async with pool().acquire() as conn:
        async with conn.transaction():
            await checkout_service.sweep_stale_pending_orders(conn)

    async with pool().acquire() as conn:
        async with conn.transaction():
            order = await checkout_service.create_pending_order(conn, body, customer)

    if order["amount_due"] <= 0:
        async with pool().acquire() as conn:
            async with conn.transaction():
                await checkout_service.finalize_zero_dollar_order(conn, order["id"])
        return CheckoutSessionOut(url=None, order_number=order["number"])

    line_items = stripe_service.build_line_items(
        order["lines"], order["shipping_amount"], order["shipping_method_name"], order["gift_card_amount"]
    )

    try:
        session = stripe_service.create_checkout_session(
            line_items=line_items,
            customer_email=customer.email if customer else body.email,
            client_reference_id=str(order["id"]),
            success_url=f"{body.success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=body.cancel_url,
        )
    except HTTPException:
        # Nothing for the customer to pay for yet -- cancel (not delete) so
        # any gift card hold / stock reservation gets released and the
        # attempt stays visible in the admin panel rather than disappearing.
        async with pool().acquire() as conn:
            async with conn.transaction():
                await checkout_service.cancel_pending_order(conn, order["id"], "checkout_session_failed")
        raise

    await order_repository.update_order_metadata(
        pool(), order["id"], {"stripe_checkout_session_id": session.id}
    )

    return CheckoutSessionOut(url=session.url)


@router.post("/checkout/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    event = stripe_service.verify_webhook_event(payload, sig_header)

    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        # stripe-python v16's StripeObject no longer subclasses dict -- .get()
        # raises AttributeError on it (only subscript/.to_dict() still work),
        # so convert once here and let everything downstream use plain .get().
        session = event["data"]["object"].to_dict()
        order_id = session.get("client_reference_id")
        if order_id and session.get("payment_status") == "paid":
            async with pool().acquire() as conn:
                async with conn.transaction():
                    await checkout_service.mark_order_paid(conn, uuid.UUID(order_id), session)

    return {"received": True}


@router.get("/checkout/session/{session_id}", response_model=CheckoutStatusOut)
async def get_checkout_status(session_id: str):
    row = await order_repository.find_order_by_checkout_session(pool(), session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "checkout session not found")
    if row["status"] != "pending":
        return CheckoutStatusOut(status="paid", order_number=row["number"], total_amount=row["total_amount"])

    # Still 'pending' in our DB -- either the customer hasn't paid yet, or
    # they have and the webhook just hasn't landed. Ask Stripe directly
    # rather than keep the customer guessing, and self-heal here if the
    # webhook is the thing that's late or missing entirely.
    session = stripe_service.retrieve_session(session_id)

    if session.get("payment_status") == "paid":
        async with pool().acquire() as conn:
            async with conn.transaction():
                await checkout_service.mark_order_paid(conn, row["id"], session)
        updated = await order_repository.get_order_summary(pool(), row["id"])
        return CheckoutStatusOut(status="paid", order_number=updated["number"], total_amount=updated["total_amount"])

    return CheckoutStatusOut(status="pending")
