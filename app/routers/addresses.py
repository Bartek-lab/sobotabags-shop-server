"""Customer address book. Separate from account.py's single GET /me --
these are multi-row, writable, and need the "only one default
shipping/billing address" invariant handled explicitly: the DB only
guarantees that invariant via a partial unique index (one at a time), it
doesn't let you atomically "make this one the new default" in a single
INSERT/UPDATE, so switching defaults is a clear-then-set two-step inside one
transaction.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import CurrentCustomer, get_current_customer
from app.core.db_errors import translate_db_errors
from app.db import pool

router = APIRouter(tags=["addresses"])


class AddressIn(BaseModel):
    label: str | None = None
    first_name: str
    last_name: str
    company: str | None = None
    vat_id: str | None = None
    line1: str
    line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str
    country_code: str
    phone: str | None = None
    is_default_shipping: bool = False
    is_default_billing: bool = False


class AddressOut(AddressIn):
    id: uuid.UUID


async def _clear_existing_defaults(conn, customer_id: uuid.UUID, body: AddressIn) -> None:
    if body.is_default_shipping:
        await conn.execute(
            "UPDATE customer_addresses SET is_default_shipping = false WHERE customer_id = $1",
            customer_id,
        )
    if body.is_default_billing:
        await conn.execute(
            "UPDATE customer_addresses SET is_default_billing = false WHERE customer_id = $1",
            customer_id,
        )


@router.get("/me/addresses", response_model=list[AddressOut])
async def list_addresses(customer: CurrentCustomer = Depends(get_current_customer)):
    rows = await pool().fetch(
        """
        SELECT id, label, first_name, last_name, company, vat_id, line1, line2, city,
               region, postal_code, country_code, phone, is_default_shipping, is_default_billing
        FROM customer_addresses WHERE customer_id = $1
        ORDER BY is_default_shipping DESC, is_default_billing DESC, created_at
        """,
        customer.id,
    )
    return [dict(r) for r in rows]


@router.post("/me/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
async def create_address(body: AddressIn, customer: CurrentCustomer = Depends(get_current_customer)):
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _clear_existing_defaults(conn, customer.id, body)
            async with translate_db_errors():
                row = await conn.fetchrow(
                    """
                    INSERT INTO customer_addresses
                      (customer_id, label, first_name, last_name, company, vat_id, line1, line2,
                       city, region, postal_code, country_code, phone,
                       is_default_shipping, is_default_billing)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    RETURNING id, label, first_name, last_name, company, vat_id, line1, line2, city,
                              region, postal_code, country_code, phone,
                              is_default_shipping, is_default_billing
                    """,
                    customer.id, body.label, body.first_name, body.last_name, body.company,
                    body.vat_id, body.line1, body.line2, body.city, body.region, body.postal_code,
                    body.country_code, body.phone, body.is_default_shipping, body.is_default_billing,
                )
    return dict(row)


@router.put("/me/addresses/{address_id}", response_model=AddressOut)
async def update_address(
    address_id: uuid.UUID, body: AddressIn, customer: CurrentCustomer = Depends(get_current_customer)
):
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _clear_existing_defaults(conn, customer.id, body)
            async with translate_db_errors():
                row = await conn.fetchrow(
                    """
                    UPDATE customer_addresses SET
                      label = $3, first_name = $4, last_name = $5, company = $6, vat_id = $7,
                      line1 = $8, line2 = $9, city = $10, region = $11, postal_code = $12,
                      country_code = $13, phone = $14, is_default_shipping = $15, is_default_billing = $16
                    WHERE id = $1 AND customer_id = $2
                    RETURNING id, label, first_name, last_name, company, vat_id, line1, line2, city,
                              region, postal_code, country_code, phone,
                              is_default_shipping, is_default_billing
                    """,
                    address_id, customer.id, body.label, body.first_name, body.last_name,
                    body.company, body.vat_id, body.line1, body.line2, body.city, body.region,
                    body.postal_code, body.country_code, body.phone,
                    body.is_default_shipping, body.is_default_billing,
                )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "address not found")
    return dict(row)


@router.delete("/me/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(address_id: uuid.UUID, customer: CurrentCustomer = Depends(get_current_customer)):
    result = await pool().execute(
        "DELETE FROM customer_addresses WHERE id = $1 AND customer_id = $2", address_id, customer.id
    )
    if result == "DELETE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "address not found")
