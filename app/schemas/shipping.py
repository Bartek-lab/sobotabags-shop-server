import uuid

from pydantic import BaseModel


class ShippingMethodOut(BaseModel):
    id: uuid.UUID
    code: str
    carrier_code: str
    name: str
    requires_pickup_point: bool
    price_amount: int
    free_above_amount: int | None
