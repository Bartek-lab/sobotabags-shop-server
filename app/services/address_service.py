"""Customer address book business logic: the "only one default
shipping/billing address at a time" rule. Separate from account.py's single
GET /me -- these are multi-row and writable."""

import uuid

from fastapi import HTTPException, status

from app.core.db_errors import translate_db_errors
from app.db import pool
from app.repositories import address_repository
from app.schemas.address import AddressIn, AddressOut


async def list_addresses(customer_id: uuid.UUID) -> list[AddressOut]:
    rows = await address_repository.list_for_customer(pool(), customer_id)
    return [AddressOut(**dict(r)) for r in rows]


async def create_address(customer_id: uuid.UUID, body: AddressIn) -> AddressOut:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await address_repository.clear_defaults(conn, customer_id, body)
            async with translate_db_errors():
                row = await address_repository.insert(conn, customer_id, body)
    return AddressOut(**dict(row))


async def update_address(customer_id: uuid.UUID, address_id: uuid.UUID, body: AddressIn) -> AddressOut:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await address_repository.clear_defaults(conn, customer_id, body)
            async with translate_db_errors():
                row = await address_repository.update(conn, address_id, customer_id, body)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "address not found")
    return AddressOut(**dict(row))


async def delete_address(customer_id: uuid.UUID, address_id: uuid.UUID) -> None:
    deleted = await address_repository.delete(pool(), address_id, customer_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "address not found")
