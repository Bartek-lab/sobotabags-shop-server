"""Data access for orders/order_lines/order_addresses/payments. No business
rules -- status-transition logic (pending -> placed/cancelled, idempotency)
lives in services/checkout_service.py."""

import uuid
from typing import Any

LOCALE = "pl"
CURRENCY = "PLN"


async def find_stale_pending_order_ids(conn, stale_minutes: int) -> list[uuid.UUID]:
    rows = await conn.fetch(
        f"SELECT id FROM orders WHERE status = 'pending' "
        f"AND created_at < now() - interval '{stale_minutes} minutes'"
    )
    return [r["id"] for r in rows]


async def mark_order_cancelled(conn, order_id: uuid.UUID, reason: str) -> bool:
    """Only matches a still-'pending' order -- the WHERE clause is what
    makes repeated cancel attempts (sweep + webhook race, etc.) idempotent."""
    updated = await conn.fetchval(
        """
        UPDATE orders SET status = 'cancelled', cancelled_at = now(), cancel_reason = $2
        WHERE id = $1 AND status = 'pending'
        RETURNING id
        """,
        order_id, reason,
    )
    return updated is not None


async def mark_order_placed_paid(conn, order_id: uuid.UUID) -> bool:
    """Only matches a still-'pending' order -- what makes a retried webhook
    or the success-page fallback racing it a harmless no-op the second time."""
    updated = await conn.fetchval(
        """
        UPDATE orders SET status = 'placed', payment_status = 'paid', placed_at = now()
        WHERE id = $1 AND status = 'pending'
        RETURNING id
        """,
        order_id,
    )
    return updated is not None


async def get_reserved_order_lines(conn, order_id: uuid.UUID) -> list:
    return await conn.fetch(
        "SELECT variant_id, quantity FROM order_lines WHERE order_id = $1 AND stock_reserved",
        order_id,
    )


async def insert_order(
    conn, *, customer_id: uuid.UUID | None, email: str, phone: str | None,
    subtotal_amount: int, discount_amount: int, shipping_amount: int, tax_amount: int, total_amount: int,
    shipping_method_id: uuid.UUID, shipping_method_code: str, shipping_method_name: str,
    shipping_carrier_code: str, pickup_point: dict | None,
):
    return await conn.fetchrow(
        """
        INSERT INTO orders
          (customer_id, email, phone, locale, currency, status, payment_status,
           prices_include_tax, subtotal_amount, discount_amount, shipping_amount, tax_amount, total_amount,
           shipping_method_id, shipping_method_code, shipping_method_name, shipping_carrier_code, pickup_point)
        VALUES ($1, $2, $3, $4, $5, 'pending', 'unpaid', true, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15)
        RETURNING id, number
        """,
        customer_id, email, phone, LOCALE, CURRENCY, subtotal_amount, discount_amount, shipping_amount,
        tax_amount, total_amount, shipping_method_id, shipping_method_code, shipping_method_name,
        shipping_carrier_code, pickup_point,
    )


async def insert_order_line(conn, order_id: uuid.UUID, line: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO order_lines
          (order_id, product_id, variant_id, sku, product_name, variant_label,
           attributes, quantity, unit_price_amount, subtotal_amount, discount_amount,
           tax_rate, tax_amount, total_amount, stock_reserved)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """,
        order_id, line["product_id"], line["variant_id"], line["sku"], line["product_name"],
        line["variant_label"], line["attributes"], line["quantity"], line["unit_price"],
        line["subtotal"], line["discount_amount"], line["tax_rate"], line["tax_amount"],
        line["total_amount"], line["stock_reserved"],
    )


async def insert_order_address(conn, order_id: uuid.UUID, address) -> None:
    await conn.execute(
        """
        INSERT INTO order_addresses
          (order_id, kind, first_name, last_name, company, line1, line2, city,
           region, postal_code, country_code, phone)
        VALUES ($1, 'shipping', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        order_id, address.first_name, address.last_name, address.company, address.line1,
        address.line2, address.city, address.region, address.postal_code, address.country_code,
        address.phone,
    )


async def update_order_metadata(conn, order_id: uuid.UUID, metadata: dict) -> None:
    await conn.execute("UPDATE orders SET metadata = $2 WHERE id = $1", order_id, metadata)


async def find_order_by_checkout_session(conn, session_id: str):
    return await conn.fetchrow(
        """
        SELECT id, number, status, total_amount FROM orders
        WHERE metadata ->> 'stripe_checkout_session_id' = $1
        """,
        session_id,
    )


async def get_order_summary(conn, order_id: uuid.UUID):
    return await conn.fetchrow("SELECT number, total_amount FROM orders WHERE id = $1", order_id)


async def insert_payment(
    conn, *, order_id: uuid.UUID, provider: str, provider_payment_id: str, method_type: str | None,
    amount: int, card_brand: str | None = None, card_last4: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO payments
          (order_id, currency, provider, provider_payment_id, method_type, status,
           amount, amount_captured, card_brand, card_last4, idempotency_key)
        VALUES ($1,$2,$3,$4,$5,'succeeded',$6,$6,$7,$8,$4)
        ON CONFLICT (provider, provider_payment_id) DO NOTHING
        """,
        order_id, CURRENCY, provider, provider_payment_id, method_type, amount, card_brand, card_last4,
    )
