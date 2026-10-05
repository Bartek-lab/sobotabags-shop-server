from fastapi import APIRouter, Depends

from app.auth import CurrentCustomer, get_current_customer

router = APIRouter(tags=["account"])


@router.get("/me", response_model=CurrentCustomer)
async def me(current: CurrentCustomer = Depends(get_current_customer)):
    return current
