async def list_active_shipping_methods(conn) -> list:
    return await conn.fetch(
        """
        SELECT id, code, carrier_code, name, requires_pickup_point, price_amount, free_above_amount
        FROM shipping_methods WHERE is_active ORDER BY sort_order
        """
    )
