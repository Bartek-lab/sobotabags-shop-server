"""Public, unauthenticated: the storefront needs to list available shipping
methods before checkout even knows who's buying."""

from fastapi import APIRouter

from app.schemas.shipping import ShippingMethodOut
from app.services import shipping_service

router = APIRouter(tags=["shipping"])


@router.get("/shipping-methods", response_model=list[ShippingMethodOut])
async def list_shipping_methods():
    return await shipping_service.list_active_methods()
