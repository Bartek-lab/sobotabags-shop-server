"""No service needed: Depends(get_current_customer) already is the entire
'business logic' of this endpoint."""

from fastapi import APIRouter, Depends

from app.auth import get_current_customer
from app.schemas.customer import CurrentCustomer

router = APIRouter(tags=["account"])


@router.get("/me", response_model=CurrentCustomer)
async def me(current: CurrentCustomer = Depends(get_current_customer)):
    return current
