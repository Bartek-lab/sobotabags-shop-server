"""Contact form. Public, unauthenticated -- routing only, see
services/contact_service.py for the actual send logic."""

from fastapi import APIRouter, Request

from app.rate_limit import limiter
from app.schemas.contact import ContactIn, ContactOut
from app.services import contact_service

router = APIRouter(tags=["contact"])


@router.post("/contact", response_model=ContactOut, status_code=201)
@limiter.limit("5/minute")
async def submit_contact_form(request: Request, body: ContactIn):
    ticket_id = await contact_service.submit(body.name, body.email, body.phone, body.topic, body.message)
    return ContactOut(ticket_id=ticket_id)
