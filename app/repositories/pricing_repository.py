"""Data access for checkout-time pricing: sellable variants, stock, shipping
methods, tax rates. Separate from catalog_repository.py (storefront
browsing) because these queries have a different consumption pattern --
row-level locking for stock reservation, not plain reads -- and are only
ever used from the pricing/checkout flow, never from product browsing.
"""

import uuid
from datetime import date

LOCALE = "pl"
CURRENCY = "PLN"


async def load_sellable_variants(conn, variant_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not variant_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT v.id AS variant_id, v.sku, v.product_id, p.tax_class_id,
               pt.name AS product_name, vp.amount AS price,
               (SELECT ma.storage_key FROM product_media pm
                JOIN media_assets ma ON ma.id = pm.asset_id
                WHERE pm.product_id = p.id ORDER BY pm.sort_order LIMIT 1) AS image_key
        FROM product_variants v
        JOIN products p ON p.id = v.product_id AND p.status = 'active'
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        LEFT JOIN variant_prices vp ON vp.variant_id = v.id AND vp.currency = $2
        WHERE v.id = ANY($3::uuid[]) AND v.is_active
        """,
        LOCALE, CURRENCY, variant_ids,
    )
    return {r["variant_id"]: dict(r) for r in rows}


async def load_variant_options(conn, variant_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not variant_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT vao.variant_id, a.code AS attribute_code,
               ao.code AS option_code, coalesce(aot.label, ao.code) AS option_label
        FROM variant_attribute_options vao
        JOIN attributes a ON a.id = vao.attribute_id
        JOIN attribute_options ao ON ao.id = vao.option_id
        LEFT JOIN attribute_option_translations aot ON aot.option_id = ao.id AND aot.locale = $1
        WHERE vao.variant_id = ANY($2::uuid[])
        """,
        LOCALE, variant_ids,
    )
    out: dict[uuid.UUID, list[dict]] = {}
    for r in rows:
        out.setdefault(r["variant_id"], []).append(
            {"code": r["attribute_code"], "value": r["option_code"], "label": r["option_label"]}
        )
    return out


async def get_variant_stock(conn, variant_id: uuid.UUID, *, lock: bool):
    lock_clause = "FOR UPDATE" if lock else ""
    return await conn.fetchrow(
        f'SELECT quantity_on_hand, allow_backorder, track_inventory FROM product_variants '
        f'WHERE id = $1 {lock_clause}',
        variant_id,
    )


async def decrement_variant_stock(conn, variant_id: uuid.UUID, qty: int) -> None:
    await conn.execute(
        "UPDATE product_variants SET quantity_on_hand = quantity_on_hand - $1 WHERE id = $2",
        qty, variant_id,
    )


async def increment_variant_stock(conn, variant_id: uuid.UUID, qty: int) -> None:
    await conn.execute(
        "UPDATE product_variants SET quantity_on_hand = quantity_on_hand + $1 WHERE id = $2",
        qty, variant_id,
    )


async def get_active_shipping_method(conn, shipping_method_id: uuid.UUID):
    return await conn.fetchrow(
        """
        SELECT id, code, carrier_code, name, requires_pickup_point, price_amount, free_above_amount
        FROM shipping_methods WHERE id = $1 AND is_active
        """,
        shipping_method_id,
    )


async def get_tax_rate(conn, tax_class_id: uuid.UUID, country_code: str, as_of: date) -> float | None:
    rate = await conn.fetchval(
        """
        SELECT rate FROM tax_rates
        WHERE tax_class_id = $1 AND country_code = $2
          AND valid_from <= $3 AND (valid_to IS NULL OR valid_to > $3)
        ORDER BY valid_from DESC LIMIT 1
        """,
        tax_class_id, country_code, as_of,
    )
    return float(rate) if rate is not None else None
