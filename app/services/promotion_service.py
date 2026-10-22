"""Promo code validation rules. Resolving a code means checking it against
every rule it carries (active, date range, usage limits, account/first-order
requirements) and computing the discount it yields -- all of which is
business logic, not just a lookup, which is why this is a service and not a
thin pass-through over promotion_repository."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.repositories import promotion_repository
from app.schemas.customer import CurrentCustomer

CURRENCY = "PLN"


async def resolve_promo(
    conn, code: str, customer: CurrentCustomer | None, email: str | None, subtotal: int, *, lock: bool
) -> dict:
    """Raises HTTPException with a clear reason on any invalid state. Caller
    decides whether that's fatal (real checkout) or just surfaced as
    `promo_error` (preview)."""
    row = await promotion_repository.find_promo_code(conn, code, lock=lock)
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown promo code")
    if not row["code_is_active"] or not row["promo_is_active"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code is no longer active")

    now = datetime.now(timezone.utc)
    if row["starts_at"] and row["starts_at"] > now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code is not active yet")
    if row["ends_at"] and row["ends_at"] <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code has expired")
    if not row["applies_to_all_items"]:
        # promotion_products/categories/collections-scoped promos aren't
        # evaluated here -- reject explicitly rather than silently applying
        # a scoped discount to the whole cart.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "this promo code only applies to specific products and isn't supported at checkout yet",
        )
    if row["registered_customers_only"] and customer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this promo code requires an account -- please log in")

    customer_id: uuid.UUID | None = customer.id if customer else None

    if row["usage_limit"] is not None:
        used = await promotion_repository.count_promotion_usage(conn, row["promotion_id"])
        if used >= row["usage_limit"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code usage limit reached")

    if row["code_usage_limit"] is not None:
        used = await promotion_repository.count_promotion_code_usage(conn, row["code_id"])
        if used >= row["code_usage_limit"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code usage limit reached")

    if row["usage_limit_per_customer"] is not None:
        used = await promotion_repository.count_customer_promotion_usage(
            conn, row["promotion_id"], customer_id, email
        )
        if used >= row["usage_limit_per_customer"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you've already used this promo code")

    if row["first_order_only"]:
        has_order = await promotion_repository.customer_has_placed_order(conn, customer_id, email)
        if has_order:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "this promo code is for first orders only")

    discount_amount = 0
    free_shipping = False
    if row["type"] == "percentage":
        discount_amount = round(subtotal * float(row["percentage"]) / 100)
    elif row["type"] == "fixed_amount":
        cv = await promotion_repository.get_fixed_amount_value(conn, row["promotion_id"])
        if cv is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code is not configured for this currency")
        if cv["min_subtotal"] is not None and subtotal < cv["min_subtotal"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"this promo code requires a minimum order of {cv['min_subtotal'] / 100:.2f} {CURRENCY}",
            )
        discount_amount = min(cv["amount_off"], subtotal)
    elif row["type"] == "free_shipping":
        free_shipping = True

    return {
        "promotion_id": row["promotion_id"], "code_id": row["code_id"], "name": row["name"],
        "discount_amount": discount_amount, "free_shipping": free_shipping,
    }
