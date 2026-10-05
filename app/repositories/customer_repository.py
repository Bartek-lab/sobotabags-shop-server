async def find_by_id(conn, customer_id: str):
    return await conn.fetchrow(
        "SELECT id, email, first_name, last_name FROM customers WHERE id = $1", customer_id
    )
