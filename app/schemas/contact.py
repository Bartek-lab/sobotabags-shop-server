from pydantic import BaseModel


class ContactIn(BaseModel):
    name: str
    email: str
    phone: str | None = None
    topic: str
    message: str


class ContactOut(BaseModel):
    ticket_id: str
