"""Public, unauthenticated: the storefront needs to list available shipping
methods before checkout even knows who's buying."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import pool

router = APIRouter(tags=["shipping"])


class ShippingMethodOut(BaseModel):
    id: uuid.UUID
    code: str
    carrier_code: str
    name: str
    requires_pickup_point: bool
    price_amount: int
    free_above_amount: int | None


@router.get("/shipping-methods", response_model=list[ShippingMethodOut])
async def list_shipping_methods():
    rows = await pool().fetch(
        """
        SELECT id, code, carrier_code, name, requires_pickup_point, price_amount, free_above_amount
        FROM shipping_methods WHERE is_active ORDER BY sort_order
        """
    )
    return [dict(r) for r in rows]
