import uuid

from pydantic import BaseModel


class CurrentCustomer(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None
