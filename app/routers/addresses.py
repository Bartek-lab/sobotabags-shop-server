"""Customer address book. Routing only -- the "one default at a time" rule
lives in app/services/address_service.py."""

import uuid

from fastapi import APIRouter, Depends, status

from app.auth import get_current_customer
from app.schemas.address import AddressIn, AddressOut
from app.schemas.customer import CurrentCustomer
from app.services import address_service

router = APIRouter(tags=["addresses"])


@router.get("/me/addresses", response_model=list[AddressOut])
async def list_addresses(customer: CurrentCustomer = Depends(get_current_customer)):
    return await address_service.list_addresses(customer.id)


@router.post("/me/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
async def create_address(body: AddressIn, customer: CurrentCustomer = Depends(get_current_customer)):
    return await address_service.create_address(customer.id, body)


@router.put("/me/addresses/{address_id}", response_model=AddressOut)
async def update_address(
    address_id: uuid.UUID, body: AddressIn, customer: CurrentCustomer = Depends(get_current_customer)
):
    return await address_service.update_address(customer.id, address_id, body)


@router.delete("/me/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(address_id: uuid.UUID, customer: CurrentCustomer = Depends(get_current_customer)):
    await address_service.delete_address(customer.id, address_id)
