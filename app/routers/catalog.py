"""Public, unauthenticated storefront catalog endpoints. Storefront-shaped
responses, not raw generic CRUD -- each endpoint assembles exactly what the
Next.js UI needs (joined translations, computed display price, resolved
variant axis labels) rather than exposing the admin's table-per-table shape.

locale and currency are hardcoded to 'pl'/'PLN' for this pass -- ksobota-shop
is a single-locale, single-currency storefront; multi-locale/currency is a
later concern, not something to speculatively parameterize now.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.db import pool
from app.storage import public_url

router = APIRouter(tags=["catalog"])

LOCALE = "pl"
CURRENCY = "PLN"
LOW_STOCK_THRESHOLD = 5


class Category(BaseModel):
    id: uuid.UUID
    code: str
    parent_id: uuid.UUID | None
    name: str
    slug: str


@router.get("/categories", response_model=list[Category])
async def list_categories():
    rows = await pool().fetch(
        """
        SELECT c.id, c.code, c.parent_id, ct.name, ct.slug
        FROM categories c
        JOIN category_translations ct ON ct.category_id = c.id AND ct.locale = $1
        WHERE c.is_active
        ORDER BY c.sort_order
        """,
        LOCALE,
    )
    return [dict(r) for r in rows]


class ProductListItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    short_description: str | None
    price_from: int | None  # minor units; None if no active variant has a price yet
    image_url: str | None
    category_slug: str | None
    category_name: str | None


@router.get("/products", response_model=list[ProductListItem])
async def list_products(
    response: Response,
    category: str | None = Query(None, description="category slug"),
    sort: str = Query("newest", pattern="^(newest|price-asc|price-desc)$"),
    start: int = Query(0, ge=0),
    end: int = Query(24, ge=1),
):
    # "featured" from the UI's SortOption type isn't backed by any column in
    # this schema (no is_featured flag exists) -- newest is the honest
    # default rather than inventing a fake signal to sort by.
    order_sql = {
        "price-asc": "price_from ASC NULLS LAST",
        "price-desc": "price_from DESC NULLS LAST",
        "newest": "p.created_at DESC",
    }[sort]

    # The list query and the count query reference different numbers of
    # placeholders (count never needs $2/currency), so each gets its own
    # category clause with its own correctly-numbered placeholder -- sharing
    # one templated string between two differently-shaped queries is exactly
    # how you end up passing N params to a query that only has N-1
    # placeholders, which Postgres rejects outright.
    list_category_sql = ""
    count_category_sql = ""
    if category:
        list_category_sql = """
          AND p.id IN (
            SELECT pc.product_id FROM product_categories pc
            JOIN categories cat ON cat.id = pc.category_id
            JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
            WHERE ct.slug = $3
          )
        """
        count_category_sql = """
          AND p.id IN (
            SELECT pc.product_id FROM product_categories pc
            JOIN categories cat ON cat.id = pc.category_id
            JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
            WHERE ct.slug = $2
          )
        """

    limit, offset = max(end - start, 0), start
    list_params: list[Any] = [LOCALE, CURRENCY, *([category] if category else []), limit, offset]

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
        LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}
    """
    rows = await pool().fetch(query, *list_params)

    count_params: list[Any] = [LOCALE, *([category] if category else [])]
    count_query = f"""
        SELECT count(*) FROM products p
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        WHERE p.status = 'active' {count_category_sql}
    """
    total = await pool().fetchval(count_query, *count_params)
    response.headers["X-Total-Count"] = str(total)

    return [
        ProductListItem(
            id=r["id"], slug=r["slug"], name=r["name"], short_description=r["short_description"],
            price_from=r["price_from"], category_slug=r["category_slug"],
            category_name=r["category_name"],
            image_url=public_url(r["image_key"]) if r["image_key"] else None,
        )
        for r in rows
    ]


class VariantOption(BaseModel):
    attribute_code: str
    attribute_name: str
    option_code: str
    option_label: str


class Variant(BaseModel):
    id: uuid.UUID
    sku: str
    price: int | None
    compare_at_price: int | None
    available: bool
    low_stock: bool  # quantity_on_hand is low but not zero -- show "only N left" urgency, not the exact count
    options: list[VariantOption]


class AttributeValue(BaseModel):
    code: str
    name: str
    value: str


class ProductDetail(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    short_description: str | None
    description: str | None
    category_slug: str | None
    images: list[str]
    variants: list[Variant]
    attributes: list[AttributeValue]
    # Same shape as the main list (ProductListItem) -- related products
    # render through the same ProductCard on the frontend, so they need the
    # same fields (price, category) rather than a leaner, incompatible shape.
    related: list[ProductListItem]


@router.get("/products/{slug}", response_model=ProductDetail)
async def get_product(slug: str):
    product = await pool().fetchrow(
        """
        SELECT p.id, pt.slug, pt.name, pt.short_description, pt.description
        FROM products p
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        WHERE pt.slug = $2 AND p.status = 'active'
        """,
        LOCALE, slug,
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    product_id = product["id"]

    images = await pool().fetch(
        """
        SELECT ma.storage_key FROM product_media pm
        JOIN media_assets ma ON ma.id = pm.asset_id
        WHERE pm.product_id = $1 ORDER BY pm.sort_order
        """,
        product_id,
    )

    category_slug = await pool().fetchval(
        """
        SELECT ct.slug FROM product_categories pc
        JOIN categories cat ON cat.id = pc.category_id
        JOIN category_translations ct ON ct.category_id = cat.id AND ct.locale = $1
        WHERE pc.product_id = $2 ORDER BY pc.is_primary DESC LIMIT 1
        """,
        LOCALE, product_id,
    )

    variant_rows = await pool().fetch(
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
    variant_ids = [r["id"] for r in variant_rows]

    option_rows = (
        await pool().fetch(
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
        if variant_ids
        else []
    )
    options_by_variant: dict[uuid.UUID, list[VariantOption]] = {}
    for r in option_rows:
        options_by_variant.setdefault(r["variant_id"], []).append(
            VariantOption(
                attribute_code=r["attribute_code"],
                attribute_name=r["attribute_name"] or r["attribute_code"],
                option_code=r["option_code"],
                option_label=r["option_label"] or r["option_code"],
            )
        )

    variants = [
        Variant(
            id=r["id"], sku=r["sku"], price=r["amount"], compare_at_price=r["compare_at_amount"],
            available=(
                not r["track_inventory"] or r["allow_backorder"] or r["quantity_on_hand"] > 0
            ),
            low_stock=(
                r["track_inventory"] and not r["allow_backorder"]
                and 0 < r["quantity_on_hand"] <= LOW_STOCK_THRESHOLD
            ),
            options=options_by_variant.get(r["id"], []),
        )
        for r in variant_rows
    ]

    # Product-level display attributes (e.g. material): scalar values and
    # select/multiselect options, generically -- not hardcoded to one field.
    attr_value_rows = await pool().fetch(
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
    attr_option_rows = await pool().fetch(
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
    attributes = [
        AttributeValue(code=r["code"], name=r["name"] or r["code"], value=r["value"])
        for r in [*attr_value_rows, *attr_option_rows]
        if r["value"]
    ]

    related_rows = (
        await pool().fetch(
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
            LOCALE, category_slug, product_id, CURRENCY,
        )
        if category_slug
        else []
    )

    return ProductDetail(
        id=product["id"], slug=product["slug"], name=product["name"],
        short_description=product["short_description"], description=product["description"],
        category_slug=category_slug,
        images=[public_url(r["storage_key"]) for r in images],
        variants=variants,
        attributes=attributes,
        related=[
            ProductListItem(
                id=r["id"], slug=r["slug"], name=r["name"], short_description=r["short_description"],
                price_from=r["price_from"], category_slug=r["category_slug"],
                category_name=r["category_name"],
                image_url=public_url(r["image_key"]) if r["image_key"] else None,
            )
            for r in related_rows
        ],
    )
