"""Gift card redemption rules and payment reconciliation."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.repositories import gift_card_repository, order_repository

CURRENCY = "PLN"


async def resolve_gift_card(conn, code: str, *, lock: bool) -> dict:
    gc = await gift_card_repository.find_gift_card(conn, code, lock=lock)
    if gc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown gift card code")
    if gc["status"] != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card is not active")
    if gc["expires_at"] is not None and gc["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card has expired")
    if gc["currency"] != CURRENCY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card currency does not match")
    if gc["balance_amount"] <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card has no remaining balance")
    return dict(gc)


async def redeem(conn, gift_card_id: uuid.UUID, amount: int, order_id: uuid.UUID) -> None:
    """Reserves the balance immediately (order-creation time), not at
    payment confirmation -- see checkout_service for why."""
    await gift_card_repository.insert_gift_card_transaction(
        conn, gift_card_id, -amount, "redeemed", order_id, "checkout"
    )


async def refund(conn, order_id: uuid.UUID, reason: str) -> None:
    """Reverses whatever was reserved for this order, if anything. Safe to
    call for an order that never used a gift card (no-op)."""
    gc_tx = await gift_card_repository.get_redeemed_transaction(conn, order_id)
    if gc_tx is None:
        return
    await gift_card_repository.insert_gift_card_transaction(
        conn, gc_tx["gift_card_id"], gc_tx["amount"], "refunded", order_id, f"checkout {reason}"
    )


async def record_payment_if_redeemed(conn, order_id: uuid.UUID) -> None:
    """The gift card balance was already reserved at order-creation time
    (see checkout_service.create_pending_order); this just adds the matching
    `payments` row once the order is confirmed paid, so SUM(payments.amount)
    for an order always equals its total_amount regardless of how many
    instruments paid for it -- a plain reporting row, no additional ledger
    effect."""
    gc_tx = await gift_card_repository.get_redeemed_transaction(conn, order_id)
    if gc_tx is None:
        return
    await order_repository.insert_payment(
        conn, order_id=order_id, provider="gift_card", provider_payment_id=f"giftcard:{order_id}",
        method_type="gift_card", amount=gc_tx["amount"],
    )
