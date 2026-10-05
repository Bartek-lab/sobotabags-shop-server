import uuid

from app.schemas.address import AddressIn

_RETURNING = """
    id, label, first_name, last_name, company, vat_id, line1, line2, city,
    region, postal_code, country_code, phone, is_default_shipping, is_default_billing
"""


async def list_for_customer(conn, customer_id: uuid.UUID) -> list:
    return await conn.fetch(
        f"""
        SELECT {_RETURNING}
        FROM customer_addresses WHERE customer_id = $1
        ORDER BY is_default_shipping DESC, is_default_billing DESC, created_at
        """,
        customer_id,
    )


async def clear_defaults(conn, customer_id: uuid.UUID, body: AddressIn) -> None:
    """The DB's partial unique indexes only guarantee "one default at a
    time," not "atomically make this one the new default" -- this is the
    clear-then-set half of that, always called inside the same transaction
    as the insert/update."""
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


async def insert(conn, customer_id: uuid.UUID, body: AddressIn):
    return await conn.fetchrow(
        f"""
        INSERT INTO customer_addresses
          (customer_id, label, first_name, last_name, company, vat_id, line1, line2,
           city, region, postal_code, country_code, phone,
           is_default_shipping, is_default_billing)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        RETURNING {_RETURNING}
        """,
        customer_id, body.label, body.first_name, body.last_name, body.company,
        body.vat_id, body.line1, body.line2, body.city, body.region, body.postal_code,
        body.country_code, body.phone, body.is_default_shipping, body.is_default_billing,
    )


async def update(conn, address_id: uuid.UUID, customer_id: uuid.UUID, body: AddressIn):
    return await conn.fetchrow(
        f"""
        UPDATE customer_addresses SET
          label = $3, first_name = $4, last_name = $5, company = $6, vat_id = $7,
          line1 = $8, line2 = $9, city = $10, region = $11, postal_code = $12,
          country_code = $13, phone = $14, is_default_shipping = $15, is_default_billing = $16
        WHERE id = $1 AND customer_id = $2
        RETURNING {_RETURNING}
        """,
        address_id, customer_id, body.label, body.first_name, body.last_name,
        body.company, body.vat_id, body.line1, body.line2, body.city, body.region,
        body.postal_code, body.country_code, body.phone,
        body.is_default_shipping, body.is_default_billing,
    )


async def delete(conn, address_id: uuid.UUID, customer_id: uuid.UUID) -> bool:
    result = await conn.execute(
        "DELETE FROM customer_addresses WHERE id = $1 AND customer_id = $2", address_id, customer_id
    )
    return result != "DELETE 0"
