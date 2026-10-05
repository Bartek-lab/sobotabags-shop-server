"""Request/response DTOs for checkout. Shared between routers (response_model,
request body parsing) and services (return shape) -- living here, not in
routers/checkout.py, is what lets services return these types without
creating a backwards services -> routers dependency.
"""

import uuid

from pydantic import BaseModel


class CheckoutItemIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int


class AddressIn(BaseModel):
    first_name: str
    last_name: str
    company: str | None = None
    line1: str
    line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str
    country_code: str
    phone: str | None = None


class PickupPointIn(BaseModel):
    point_name: str  # InPost locker code, e.g. "WAW01A"
    point_address: str | None = None
    first_name: str
    last_name: str
    phone: str | None = None


class CheckoutSessionIn(BaseModel):
    items: list[CheckoutItemIn]
    shipping_method_id: uuid.UUID
    email: str | None = None  # required for guests; ignored for logged-in customers
    address: AddressIn | None = None
    pickup_point: PickupPointIn | None = None
    promo_code: str | None = None
    gift_card_code: str | None = None
    success_url: str
    cancel_url: str


class CheckoutSessionOut(BaseModel):
    # url is None only for the zero-dollar case (gift card covers the whole
    # order) -- the frontend skips the Stripe redirect and goes straight to
    # a confirmation using order_number.
    url: str | None
    order_number: int | None = None


class CheckoutStatusOut(BaseModel):
    status: str  # "paid" | "pending"
    order_number: int | None = None
    total_amount: int | None = None


class PricingPreviewIn(BaseModel):
    items: list[CheckoutItemIn]
    shipping_method_id: uuid.UUID
    promo_code: str | None = None
    gift_card_code: str | None = None


class PricingPreviewOut(BaseModel):
    subtotal_amount: int
    discount_amount: int
    shipping_amount: int
    tax_amount: int
    total_amount: int
    gift_card_amount: int
    amount_due: int
    promo_name: str | None = None
    promo_error: str | None = None
    gift_card_error: str | None = None
