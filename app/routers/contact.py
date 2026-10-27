"""Contact form. Public, unauthenticated -- routing only, see
services/contact_service.py for the actual send logic."""

from fastapi import APIRouter

from app.schemas.contact import ContactIn, ContactOut
from app.services import contact_service

router = APIRouter(tags=["contact"])


@router.post("/contact", response_model=ContactOut, status_code=201)
async def submit_contact_form(body: ContactIn):
    ticket_id = await contact_service.submit(body.name, body.email, body.phone, body.topic, body.message)
    return ContactOut(ticket_id=ticket_id)
