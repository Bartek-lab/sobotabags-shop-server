"""Stock validation and reservation. A hard constraint, unlike promo/gift-card
codes -- a cart that can't be fulfilled isn't a valid quote regardless of
strict mode, so this always raises on insufficient stock in both preview and
real checkout."""

import uuid

from fastapi import HTTPException, status

from app.repositories import pricing_repository


async def check_and_reserve_stock(conn, lines: list[dict], *, lock: bool) -> dict[uuid.UUID, bool]:
    """Only the real checkout (lock=True) takes a row lock per distinct
    variant and actually decrements; preview (lock=False) just checks the
    current quantity_on_hand as a best-effort read -- same asymmetry as
    gift card balance checking.

    Returns which variant_ids actually had stock reserved (track_inventory
    and not allow_backorder) -- order_lines.stock_reserved is set from this
    per line, so cancellation later knows exactly what to give back without
    re-deriving it from the variant's current (possibly since-changed)
    flags.
    """
    needed: dict[uuid.UUID, int] = {}
    names: dict[uuid.UUID, str] = {}
    for line in lines:
        vid = line["variant_id"]
        needed[vid] = needed.get(vid, 0) + line["quantity"]
        names[vid] = line["product_name"]

    reserved: dict[uuid.UUID, bool] = {}
    for variant_id, qty in needed.items():
        row = await pricing_repository.get_variant_stock(conn, variant_id, lock=lock)
        if row is None or not row["track_inventory"] or row["allow_backorder"]:
            reserved[variant_id] = False
            continue
        if row["quantity_on_hand"] < qty:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{names[variant_id]} is out of stock ({row['quantity_on_hand']} left)",
            )
        if lock:
            await pricing_repository.decrement_variant_stock(conn, variant_id, qty)
        reserved[variant_id] = True
    return reserved
