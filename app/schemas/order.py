import uuid
from datetime import datetime

from pydantic import BaseModel


class OrderLineOut(BaseModel):
    product_name: str
    variant_label: str | None = None
    sku: str
    quantity: int
    unit_price_amount: int
    total_amount: int
    image_url: str | None = None


class OrderAddressOut(BaseModel):
    first_name: str
    last_name: str
    line1: str
    line2: str | None = None
    city: str
    postal_code: str
    country_code: str
    phone: str | None = None


class WithdrawalOut(BaseModel):
    id: uuid.UUID
    status: str
    requested_at: datetime


class OrderSummaryOut(BaseModel):
    id: uuid.UUID
    number: int
    status: str
    payment_status: str
    total_amount: int
    created_at: datetime
    placed_at: datetime | None = None
    item_count: int


class OrderDetailOut(BaseModel):
    id: uuid.UUID
    number: int
    status: str
    payment_status: str
    subtotal_amount: int
    discount_amount: int
    shipping_amount: int
    tax_amount: int
    total_amount: int
    created_at: datetime
    placed_at: datetime | None = None
    cancelled_at: datetime | None = None
    shipping_method_name: str | None = None
    pickup_point: dict | None = None
    address: OrderAddressOut | None = None
    lines: list[OrderLineOut]
    withdrawal: WithdrawalOut | None = None
    can_withdraw: bool


class WithdrawalIn(BaseModel):
    reason: str | None = None
