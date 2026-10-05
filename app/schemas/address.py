import uuid

from pydantic import BaseModel


class AddressIn(BaseModel):
    label: str | None = None
    first_name: str
    last_name: str
    company: str | None = None
    vat_id: str | None = None
    line1: str
    line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str
    country_code: str
    phone: str | None = None
    is_default_shipping: bool = False
    is_default_billing: bool = False


class AddressOut(AddressIn):
    id: uuid.UUID
