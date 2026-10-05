"""Data access for promo code resolution and usage tracking. Validation
rules live in services/promotion_service.py -- this is just queries."""

import uuid

CURRENCY = "PLN"


async def find_promo_code(conn, code: str, *, lock: bool):
    lock_clause = "FOR UPDATE OF pc, p" if lock else ""
    return await conn.fetchrow(
        f"""
        SELECT pc.id AS code_id, pc.is_active AS code_is_active, pc.usage_limit AS code_usage_limit,
               p.id AS promotion_id, p.name, p.type, p.percentage, p.applies_to_all_items,
               p.is_active AS promo_is_active, p.usage_limit, p.usage_limit_per_customer,
               p.registered_customers_only, p.first_order_only, p.starts_at, p.ends_at
        FROM promotion_codes pc
        JOIN promotions p ON p.id = pc.promotion_id
        WHERE pc.code = $1
        {lock_clause}
        """,
        code,
    )


async def count_promotion_usage(conn, promotion_id: uuid.UUID) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
        "WHERE op.promotion_id = $1 AND o.status <> 'cancelled'",
        promotion_id,
    )


async def count_promotion_code_usage(conn, code_id: uuid.UUID) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
        "WHERE op.promotion_code_id = $1 AND o.status <> 'cancelled'",
        code_id,
    )


async def count_customer_promotion_usage(
    conn, promotion_id: uuid.UUID, customer_id: uuid.UUID | None, email: str | None
) -> int:
    if customer_id is not None:
        return await conn.fetchval(
            "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
            "WHERE op.promotion_id = $1 AND o.customer_id = $2 AND o.status <> 'cancelled'",
            promotion_id, customer_id,
        )
    return await conn.fetchval(
        "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
        "WHERE op.promotion_id = $1 AND o.customer_id IS NULL AND o.email = $2 AND o.status <> 'cancelled'",
        promotion_id, email,
    )


async def customer_has_placed_order(conn, customer_id: uuid.UUID | None, email: str | None) -> bool:
    if customer_id is not None:
        return await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM orders WHERE customer_id = $1 AND status IN ('placed', 'completed'))",
            customer_id,
        )
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM orders WHERE customer_id IS NULL AND email = $1 "
        "AND status IN ('placed', 'completed'))",
        email,
    )


async def get_fixed_amount_value(conn, promotion_id: uuid.UUID):
    return await conn.fetchrow(
        "SELECT amount_off, min_subtotal FROM promotion_currency_values "
        "WHERE promotion_id = $1 AND currency = $2",
        promotion_id, CURRENCY,
    )


async def insert_order_promotion(
    conn, order_id: uuid.UUID, promotion_id: uuid.UUID, code_id: uuid.UUID, discount_amount: int
) -> None:
    await conn.execute(
        "INSERT INTO order_promotions (order_id, promotion_id, promotion_code_id, discount_amount) "
        "VALUES ($1, $2, $3, $4)",
        order_id, promotion_id, code_id, discount_amount,
    )
