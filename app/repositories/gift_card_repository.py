"""Data access for gift cards and their (append-only) transaction ledger."""

import uuid


async def find_gift_card(conn, code: str, *, lock: bool):
    lock_clause = "FOR UPDATE" if lock else ""
    return await conn.fetchrow(f'SELECT * FROM gift_cards WHERE code = $1 {lock_clause}', code)


async def insert_gift_card_transaction(
    conn, gift_card_id: uuid.UUID, amount_delta: int, reason: str, order_id: uuid.UUID, note: str
) -> None:
    await conn.execute(
        "INSERT INTO gift_card_transactions (gift_card_id, amount_delta, reason, order_id, note) "
        "VALUES ($1, $2, $3, $4, $5)",
        gift_card_id, amount_delta, reason, order_id, note,
    )


async def get_redeemed_transaction(conn, order_id: uuid.UUID):
    """The positive-amount view of whatever was reserved for this order at
    checkout time (the stored row itself is a negative amount_delta)."""
    return await conn.fetchrow(
        "SELECT gift_card_id, -amount_delta AS amount FROM gift_card_transactions "
        "WHERE order_id = $1 AND reason = 'redeemed'",
        order_id,
    )
