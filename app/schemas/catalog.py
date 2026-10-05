"""Storefront-shaped catalog DTOs -- not raw generic CRUD, each one assembles
exactly what the Next.js UI needs (joined translations, computed display
price, resolved variant axis labels) rather than exposing the admin's
table-per-table shape."""

import uuid

from pydantic import BaseModel


class Category(BaseModel):
    id: uuid.UUID
    code: str
    parent_id: uuid.UUID | None
    name: str
    slug: str


class ProductListItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    short_description: str | None
    price_from: int | None  # minor units; None if no active variant has a price yet
    image_url: str | None
    category_slug: str | None
    category_name: str | None


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
