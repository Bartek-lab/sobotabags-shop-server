"""Customer order history + withdrawal. Routing only -- see
services/order_service.py for eligibility rules and the withdrawal flow."""

import uuid

from fastapi import APIRouter, Depends, status

from app.auth import get_current_customer
from app.schemas.customer import CurrentCustomer
from app.schemas.order import OrderDetailOut, OrderSummaryOut, WithdrawalIn, WithdrawalOut
from app.services import order_service

router = APIRouter(tags=["orders"])


@router.get("/me/orders", response_model=list[OrderSummaryOut])
async def list_my_orders(customer: CurrentCustomer = Depends(get_current_customer)):
    return await order_service.list_my_orders(customer.id)


@router.get("/me/orders/{order_id}", response_model=OrderDetailOut)
async def get_my_order(order_id: uuid.UUID, customer: CurrentCustomer = Depends(get_current_customer)):
    return await order_service.get_my_order(customer.id, order_id)


@router.post(
    "/me/orders/{order_id}/withdrawal", response_model=WithdrawalOut, status_code=status.HTTP_201_CREATED
)
async def submit_withdrawal(
    order_id: uuid.UUID, body: WithdrawalIn, customer: CurrentCustomer = Depends(get_current_customer)
):
    return await order_service.submit_withdrawal(customer.id, order_id, body.reason)
