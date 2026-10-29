"""Storefront browsing orchestration: joins repository rows into the DTOs
ksobota-shop actually consumes (computed price_from, resolved image URLs,
availability from real stock)."""

import uuid

from fastapi import HTTPException, status

from app.db import pool
from app.repositories import catalog_repository
from app.schemas.catalog import (
    AttributeValue,
    Category,
    ProductDetail,
    ProductListItem,
    Variant,
    VariantOption,
)
from app.storage import public_url

LOW_STOCK_THRESHOLD = 5

_SORT_SQL = {
    "price-asc": "price_from ASC NULLS LAST",
    "price-desc": "price_from DESC NULLS LAST",
    "newest": "p.created_at DESC",
}


async def list_categories() -> list[Category]:
    rows = await catalog_repository.list_categories(pool())
    return [Category(**dict(r)) for r in rows]


def _to_list_item(r) -> ProductListItem:
    return ProductListItem(
        id=r["id"], slug=r["slug"], name=r["name"], short_description=r["short_description"],
        price_from=r["price_from"], category_slug=r["category_slug"], category_name=r["category_name"],
        image_url=public_url(r["image_key"]) if r["image_key"] else None,
    )


async def list_products(
    *, category: str | None, bestseller: bool, sort: str, start: int, end: int
) -> tuple[list[ProductListItem], int]:
    order_sql = _SORT_SQL[sort]
    limit, offset = max(end - start, 0), start

    conn = pool()
    rows = await catalog_repository.list_products(
        conn, category=category, bestseller=bestseller, order_sql=order_sql, limit=limit, offset=offset
    )
    total = await catalog_repository.count_products(conn, category=category, bestseller=bestseller)
    return [_to_list_item(r) for r in rows], total


async def get_product_detail(slug: str) -> ProductDetail:
    conn = pool()
    product = await catalog_repository.get_product_by_slug(conn, slug)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    product_id = product["id"]

    images = await catalog_repository.list_product_images(conn, product_id)
    category_slug = await catalog_repository.get_primary_category_slug(conn, product_id)

    variant_rows = await catalog_repository.list_variants_for_display(conn, product_id)
    variant_ids = [r["id"] for r in variant_rows]
    option_rows = await catalog_repository.list_variant_options_for_display(conn, variant_ids)

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
            available=(not r["track_inventory"] or r["allow_backorder"] or r["quantity_on_hand"] > 0),
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
    attr_value_rows = await catalog_repository.list_product_attribute_values(conn, product_id)
    attr_option_rows = await catalog_repository.list_product_attribute_options(conn, product_id)
    attributes = [
        AttributeValue(code=r["code"], name=r["name"] or r["code"], value=r["value"])
        for r in [*attr_value_rows, *attr_option_rows]
        if r["value"]
    ]

    related_rows = await catalog_repository.list_related_products(conn, category_slug, product_id)

    return ProductDetail(
        id=product["id"], slug=product["slug"], name=product["name"],
        short_description=product["short_description"], description=product["description"],
        category_slug=category_slug,
        images=[public_url(r["storage_key"]) for r in images],
        variants=variants,
        attributes=attributes,
        related=[_to_list_item(r) for r in related_rows],
    )
