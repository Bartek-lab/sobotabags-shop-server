"""Customer order history + the EU-mandated digital withdrawal
("odstąpienie od umowy") action -- Directive 2023/2673, in force since
2026-06-19, requires a genuine clickable withdrawal function reachable from
wherever the customer bought, not a mailto link or a Regulamin clause.

Submitting a withdrawal only records the declaration and cancels the order --
it deliberately does NOT auto-refund (no Stripe refund execution exists yet)
or auto-restock (the item isn't physically back in hand yet). The shop owner
reviews requests via admin-api's generic CRUD on order_withdrawals and
actions them manually until that automation exists.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.core.db_errors import translate_db_errors
from app.db import pool
from app.repositories import order_repository
from app.storage import public_url

# No real "delivered" date is tracked yet (no carrier integration wired up) --
# this is a deliberately generous proxy for the 14-day legal window (allows
# for shipping time + the 14 days themselves + a buffer) that will never
# wrongly deny a legitimate request. Tighten this once real delivery
# confirmation exists.
WITHDRAWAL_WINDOW_DAYS = 30


def _line_out(row) -> dict:
    return {
        "product_name": row["product_name"],
        "variant_label": row["variant_label"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "unit_price_amount": row["unit_price_amount"],
        "total_amount": row["total_amount"],
        "image_url": public_url(row["image_key"]) if row["image_key"] else None,
    }


def _can_withdraw(order_row, has_withdrawal: bool) -> bool:
    if has_withdrawal:
        return False
    if order_row["status"] != "placed":
        return False
    if order_row["placed_at"] is None:
        return False
    deadline = order_row["placed_at"] + timedelta(days=WITHDRAWAL_WINDOW_DAYS)
    return datetime.now(timezone.utc) <= deadline


async def list_my_orders(customer_id: uuid.UUID) -> list[dict]:
    rows = await order_repository.list_orders_for_customer(pool(), customer_id)
    return [dict(r) for r in rows]


async def get_my_order(customer_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    order_row = await order_repository.get_order_for_customer(pool(), order_id, customer_id)
    if order_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")

    lines = await order_repository.list_order_lines(pool(), order_id)
    address = await order_repository.get_order_address(pool(), order_id)
    withdrawal = await order_repository.find_withdrawal(pool(), order_id)

    return {
        **dict(order_row),
        "lines": [_line_out(line) for line in lines],
        "address": dict(address) if address else None,
        "withdrawal": dict(withdrawal) if withdrawal else None,
        "can_withdraw": _can_withdraw(order_row, withdrawal is not None),
    }


async def submit_withdrawal(customer_id: uuid.UUID, order_id: uuid.UUID, reason: str | None) -> dict:
    order_row = await order_repository.get_order_for_customer(pool(), order_id, customer_id)
    if order_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")

    existing = await order_repository.find_withdrawal(pool(), order_id)
    if not _can_withdraw(order_row, existing is not None):
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "withdrawal already submitted for this order")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this order is no longer eligible for withdrawal")

    async with pool().acquire() as conn:
        async with conn.transaction():
            async with translate_db_errors():
                withdrawal = await order_repository.insert_withdrawal(conn, order_id, reason)
                cancelled = await order_repository.mark_order_withdrawn(conn, order_id)
                if not cancelled:
                    # Order moved out of 'placed' between our check above and
                    # now (race) -- raising here rolls back the insert too.
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, "order changed state, please refresh and retry"
                    )

    return dict(withdrawal)
