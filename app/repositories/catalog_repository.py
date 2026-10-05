"""Pure data access for storefront browsing (categories, product list/detail).
No business rules here -- just queries. Row shaping into response DTOs
happens in services/catalog_service.py.
"""

import uuid
from typing import Any

LOCALE = "pl"
CURRENCY = "PLN"


async def list_categories(conn) -> list:
    return await conn.fetch(
        """
        SELECT c.id, c.code, c.parent_id, ct.name, ct.slug
        FROM categories c
        JOIN category_translations ct ON ct.category_id = c.id AND ct.locale = $1
        WHERE c.is_active
        ORDER BY c.sort_order
        """,
        LOCALE,
    )


async def list_products(conn, *, category: str | None, order_sql: str, limit: int, offset: int) -> list:
    # The list query and the count query reference different numbers of
    # placeholders (count never needs $2/currency), so each gets its own
    # category clause with its own correctly-numbered placeholder -- sharing
    # one templated string between two differently-shaped queries is exactly
    # how you end up passing N params to a query that only has N-1
    # placeholders, which Postgres rejects outright.
    list_category_sql = ""
    if category:
        list_category_sql = """
          AND p.id IN (
            SELECT pc.product_id FROM product_categories pc
            JOIN categories cat ON cat.id = pc.category_id
            JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
            WHERE ct.slug = $3
          )
        """
    params: list[Any] = [LOCALE, CURRENCY, *([category] if category else []), limit, offset]
    query = f"""
        SELECT p.id, pt.slug, pt.name, pt.short_description,
          (SELECT min(vp.amount) FROM product_variants v
           JOIN variant_prices vp ON vp.variant_id = v.id
           WHERE v.product_id = p.id AND v.is_active AND vp.currency = $2) AS price_from,
          (SELECT ma.storage_key FROM product_media pm
           JOIN media_assets ma ON ma.id = pm.asset_id
           WHERE pm.product_id = p.id ORDER BY pm.sort_order LIMIT 1) AS image_key,
          (SELECT ct2.slug FROM product_categories pc2
           JOIN categories cat2 ON cat2.id = pc2.category_id
           JOIN category_translations ct2 ON ct2.category_id = cat2.id AND ct2.locale = $1
           WHERE pc2.product_id = p.id ORDER BY pc2.is_primary DESC LIMIT 1) AS category_slug,
          (SELECT ct2.name FROM product_categories pc2
           JOIN categories cat2 ON cat2.id = pc2.category_id
           JOIN category_translations ct2 ON ct2.category_id = cat2.id AND ct2.locale = $1
           WHERE pc2.product_id = p.id ORDER BY pc2.is_primary DESC LIMIT 1) AS category_name
        FROM products p
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        WHERE p.status = 'active' {list_category_sql}
        ORDER BY {order_sql}
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
    """
    return await conn.fetch(query, *params)


async def count_products(conn, *, category: str | None) -> int:
    count_category_sql = ""
    if category:
        count_category_sql = """
          AND p.id IN (
            SELECT pc.product_id FROM product_categories pc
            JOIN categories cat ON cat.id = pc.category_id
            JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
            WHERE ct.slug = $2
          )
        """
    params: list[Any] = [LOCALE, *([category] if category else [])]
    query = f"""
        SELECT count(*) FROM products p
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        WHERE p.status = 'active' {count_category_sql}
    """
    return await conn.fetchval(query, *params)


async def get_product_by_slug(conn, slug: str):
    return await conn.fetchrow(
        """
        SELECT p.id, pt.slug, pt.name, pt.short_description, pt.description
        FROM products p
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        WHERE pt.slug = $2 AND p.status = 'active'
        """,
        LOCALE, slug,
    )


async def list_product_images(conn, product_id: uuid.UUID) -> list:
    return await conn.fetch(
        """
        SELECT ma.storage_key FROM product_media pm
        JOIN media_assets ma ON ma.id = pm.asset_id
        WHERE pm.product_id = $1 ORDER BY pm.sort_order
        """,
        product_id,
    )


async def get_primary_category_slug(conn, product_id: uuid.UUID) -> str | None:
    return await conn.fetchval(
        """
        SELECT ct.slug FROM product_categories pc
        JOIN categories cat ON cat.id = pc.category_id
        JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
        WHERE pc.product_id = $2 ORDER BY pc.is_primary DESC LIMIT 1
        """,
        LOCALE, product_id,
    )


async def list_variants_for_display(conn, product_id: uuid.UUID) -> list:
    return await conn.fetch(
        """
        SELECT v.id, v.sku, vp.amount, vp.compare_at_amount,
               v.quantity_on_hand, v.allow_backorder, v.track_inventory
        FROM product_variants v
        LEFT JOIN variant_prices vp ON vp.variant_id = v.id AND vp.currency = $1
        WHERE v.product_id = $2 AND v.is_active
        ORDER BY v.sort_order
        """,
        CURRENCY, product_id,
    )


async def list_variant_options_for_display(conn, variant_ids: list[uuid.UUID]) -> list:
    if not variant_ids:
        return []
    return await conn.fetch(
        """
        SELECT vao.variant_id, a.code AS attribute_code, att.name AS attribute_name,
               ao.code AS option_code, aot.label AS option_label
        FROM variant_attribute_options vao
        JOIN attributes a ON a.id = vao.attribute_id
        LEFT JOIN attribute_translations att ON att.attribute_id = a.id AND att.locale = $1
        JOIN attribute_options ao ON ao.id = vao.option_id
        LEFT JOIN attribute_option_translations aot ON aot.option_id = ao.id AND aot.locale = $1
        WHERE vao.variant_id = ANY($2::uuid[])
        """,
        LOCALE, variant_ids,
    )


async def list_product_attribute_values(conn, product_id: uuid.UUID) -> list:
    return await conn.fetch(
        """
        SELECT a.code, att.name,
               coalesce(pav.value_text, pav.value_number::text, pav.value_boolean::text,
                        pav.value_date::text) AS value
        FROM product_attribute_values pav
        JOIN attributes a ON a.id = pav.attribute_id
        LEFT JOIN attribute_translations att ON att.attribute_id = a.id AND att.locale = $1
        WHERE pav.product_id = $2 AND a.is_visible
        """,
        LOCALE, product_id,
    )


async def list_product_attribute_options(conn, product_id: uuid.UUID) -> list:
    return await conn.fetch(
        """
        SELECT a.code, att.name, string_agg(aot.label, ', ' ORDER BY pao.sort_order) AS value
        FROM product_attribute_options pao
        JOIN attributes a ON a.id = pao.attribute_id
        LEFT JOIN attribute_translations att ON att.attribute_id = a.id AND att.locale = $1
        JOIN attribute_options ao ON ao.id = pao.option_id
        LEFT JOIN attribute_option_translations aot ON aot.option_id = ao.id AND aot.locale = $1
        WHERE pao.product_id = $2 AND a.is_visible
        GROUP BY a.code, att.name
        """,
        LOCALE, product_id,
    )


async def list_related_products(conn, category_slug: str | None, exclude_product_id: uuid.UUID) -> list:
    if not category_slug:
        return []
    return await conn.fetch(
        """
        SELECT p2.id, pt2.slug, pt2.name, pt2.short_description,
          (SELECT min(vp.amount) FROM product_variants v
           JOIN variant_prices vp ON vp.variant_id = v.id
           WHERE v.product_id = p2.id AND v.is_active AND vp.currency = $4) AS price_from,
          (SELECT ma.storage_key FROM product_media pm
           JOIN media_assets ma ON ma.id = pm.asset_id
           WHERE pm.product_id = p2.id ORDER BY pm.sort_order LIMIT 1) AS image_key,
          ct2.slug AS category_slug, ct2.name AS category_name
        FROM products p2
        JOIN product_translations pt2 ON pt2.product_id = p2.id AND pt2.locale = $1
        JOIN product_categories pc2 ON pc2.product_id = p2.id
        JOIN categories cat2 ON cat2.id = pc2.category_id
        JOIN category_translations ct2 ON ct2.category_id = cat2.id AND ct2.locale = $1
        WHERE ct2.slug = $2 AND p2.id <> $3 AND p2.status = 'active'
        LIMIT 4
        """,
        LOCALE, category_slug, exclude_product_id, CURRENCY,
    )
