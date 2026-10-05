"""Checkout: hosted Stripe Checkout Session creation + webhook, plus promo
code and gift card redemption.

The order is created as status='pending'/payment_status='unpaid' *before* the
customer ever reaches Stripe's page -- not as a side effect of payment
succeeding. This matches what orders.status's enum was built for
(draft -> pending -> placed -> completed/cancelled) and avoids a real gap: if
a webhook-only design's webhook never lands (bad endpoint config, dropped
request), a successful Stripe payment would leave no order behind at all. A
pre-created pending order is always there to reconcile, and abandoned
checkouts become visible in the admin panel instead of vanishing.

Stripe's `client_reference_id` carries our order id through the whole round
trip, so the webhook never has to re-derive cart contents or re-price
anything -- it just flips the already-correct order to paid and fills in what
only Stripe's PaymentIntent reveals (card brand/last4).

Shipping method + address (or InPost pickup point) are collected on OUR
checkout page and persisted to the order BEFORE Stripe is ever reached --
Stripe's hosted page has no concept of a Polish parcel locker, so it can't be
the one collecting this.

PROMO CODES AND GIFT CARDS -- the parts that need to be bulletproof, not just
correct on the happy path:

- A promo code's usage_limit / usage_limit_per_customer and a gift card's
  balance are limited resources two concurrent checkouts can race on. Both
  are resolved under `SELECT ... FOR UPDATE` inside the SAME transaction
  that creates the order, so a second concurrent attempt blocks until the
  first commits (and then sees the updated usage count / balance), instead
  of both succeeding and overselling a limited code or double-spending a
  gift card.
- A gift card's balance is reserved (deducted via a 'redeemed'
  gift_card_transactions row) at order-creation time, not at payment
  confirmation. Waiting until payment would mean telling Stripe to charge
  `total - gift_card_amount` and then discovering, after the fact, that the
  balance evaporated in the meantime -- an order Stripe already charged a
  fixed amount for, that we can no longer actually fund. Reserving upfront
  avoids that at the cost of needing to release the reservation if the
  checkout is abandoned, which `_sweep_stale_pending_orders` and
  `_cancel_pending_order` handle (see below) -- no cron needed, since every
  new checkout attempt opportunistically sweeps stale ones first.
- Stripe never sees a Coupon/discount object. A session-level Stripe
  discount applies to ALL line items including the separately-added shipping
  line, which would silently undercharge relative to what we recorded as
  owed. Instead, discounted/gift-card-reduced amounts are baked directly
  into the per-line prices we send Stripe, so the sum Stripe actually
  charges always exactly equals `amount_due` -- never approximately.
- If a gift card fully covers the order, Stripe is skipped entirely (a $0
  Checkout Session isn't a thing) and the order is marked paid synchronously.
"""

import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth import CurrentCustomer, get_current_customer_optional
from app.config import settings
from app.db import pool
from app.storage import public_url

router = APIRouter(tags=["checkout"])

LOCALE = "pl"
CURRENCY = "PLN"
DEFAULT_TAX_COUNTRY = "PL"  # matches the admin side's Poland-only tax config
STALE_PENDING_MINUTES = 45  # must stay comfortably above STRIPE_SESSION_EXPIRES_MINUTES
STRIPE_SESSION_EXPIRES_MINUTES = 40  # Stripe's own minimum is 30

stripe.api_key = settings.stripe_secret_key


# ---------------------------------------------------------------- schemas --

class CheckoutItemIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int


class AddressIn(BaseModel):
    first_name: str
    last_name: str
    company: str | None = None
    line1: str
    line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str
    country_code: str
    phone: str | None = None


class PickupPointIn(BaseModel):
    point_name: str  # InPost locker code, e.g. "WAW01A"
    point_address: str | None = None
    first_name: str
    last_name: str
    phone: str | None = None


class CheckoutSessionIn(BaseModel):
    items: list[CheckoutItemIn]
    shipping_method_id: uuid.UUID
    email: str | None = None  # required for guests; ignored for logged-in customers
    address: AddressIn | None = None
    pickup_point: PickupPointIn | None = None
    promo_code: str | None = None
    gift_card_code: str | None = None
    success_url: str
    cancel_url: str


class CheckoutSessionOut(BaseModel):
    # url is None only for the zero-dollar case (gift card covers the whole
    # order) -- the frontend skips the Stripe redirect and goes straight to
    # a confirmation using order_number.
    url: str | None
    order_number: int | None = None


class CheckoutStatusOut(BaseModel):
    status: str  # "paid" | "pending"
    order_number: int | None = None
    total_amount: int | None = None


class PricingPreviewIn(BaseModel):
    items: list[CheckoutItemIn]
    shipping_method_id: uuid.UUID
    promo_code: str | None = None
    gift_card_code: str | None = None


class PricingPreviewOut(BaseModel):
    subtotal_amount: int
    discount_amount: int
    shipping_amount: int
    tax_amount: int
    total_amount: int
    gift_card_amount: int
    amount_due: int
    promo_name: str | None = None
    promo_error: str | None = None
    gift_card_error: str | None = None


# ------------------------------------------------------------- utilities --

def _distribute_amount(weights: list[int], total_to_distribute: int) -> list[int]:
    """Splits total_to_distribute across items proportional to each item's
    weight, remainder landing on the last item so the sum is always exactly
    total_to_distribute -- used both for spreading a promo discount across
    order lines, and for spreading a gift card's payment coverage across
    what Stripe actually charges per line. Never returns a value exceeding
    the item's own weight (can't discount a line below zero)."""
    total_weight = sum(weights)
    remaining = total_to_distribute
    out = []
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            d = remaining
        else:
            d = round(total_to_distribute * w / total_weight) if total_weight else 0
            d = min(d, w, remaining)
        out.append(d)
        remaining -= d
    return out


async def _load_sellable_variants(conn, variant_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not variant_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT v.id AS variant_id, v.sku, v.product_id, p.tax_class_id,
               pt.name AS product_name, vp.amount AS price,
               (SELECT ma.storage_key FROM product_media pm
                JOIN media_assets ma ON ma.id = pm.asset_id
                WHERE pm.product_id = p.id ORDER BY pm.sort_order LIMIT 1) AS image_key
        FROM product_variants v
        JOIN products p ON p.id = v.product_id AND p.status = 'active'
        JOIN product_translations pt ON pt.product_id = p.id AND pt.locale = $1
        LEFT JOIN variant_prices vp ON vp.variant_id = v.id AND vp.currency = $2
        WHERE v.id = ANY($3::uuid[]) AND v.is_active
        """,
        LOCALE, CURRENCY, variant_ids,
    )
    return {r["variant_id"]: dict(r) for r in rows}


async def _check_and_reserve_stock(conn, lines: list[dict], *, lock: bool) -> dict[uuid.UUID, bool]:
    """Stock is a hard constraint, unlike promo/gift-card codes -- a cart
    that can't be fulfilled isn't a valid quote regardless of strict mode,
    so this always raises on insufficient stock in both preview and real
    checkout. Only the real checkout (lock=True) takes a row lock per
    distinct variant and actually decrements; preview (lock=False) just
    checks the current quantity_on_hand as a best-effort read -- same
    asymmetry as gift card balance checking.

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
    lock_clause = "FOR UPDATE" if lock else ""
    for variant_id, qty in needed.items():
        row = await conn.fetchrow(
            f'SELECT quantity_on_hand, allow_backorder, track_inventory FROM product_variants '
            f'WHERE id = $1 {lock_clause}',
            variant_id,
        )
        if row is None or not row["track_inventory"] or row["allow_backorder"]:
            reserved[variant_id] = False
            continue
        if row["quantity_on_hand"] < qty:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{names[variant_id]} is out of stock ({row['quantity_on_hand']} left)",
            )
        if lock:
            await conn.execute(
                "UPDATE product_variants SET quantity_on_hand = quantity_on_hand - $1 WHERE id = $2",
                qty, variant_id,
            )
        reserved[variant_id] = True
    return reserved


async def _load_variant_options(conn, variant_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not variant_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT vao.variant_id, a.code AS attribute_code,
               ao.code AS option_code, coalesce(aot.label, ao.code) AS option_label
        FROM variant_attribute_options vao
        JOIN attributes a ON a.id = vao.attribute_id
        JOIN attribute_options ao ON ao.id = vao.option_id
        LEFT JOIN attribute_option_translations aot ON aot.option_id = ao.id AND aot.locale = $1
        WHERE vao.variant_id = ANY($2::uuid[])
        """,
        LOCALE, variant_ids,
    )
    out: dict[uuid.UUID, list[dict]] = {}
    for r in rows:
        out.setdefault(r["variant_id"], []).append(
            {"code": r["attribute_code"], "value": r["option_code"], "label": r["option_label"]}
        )
    return out


# ------------------------------------------------------- promo / gift card --

async def _resolve_promo(
    conn, code: str, customer: CurrentCustomer | None, email: str | None, subtotal: int, *, lock: bool
) -> dict:
    """Raises HTTPException with a clear reason on any invalid state. Caller
    decides whether that's fatal (real checkout) or just surfaced as
    `promo_error` (preview)."""
    lock_clause = "FOR UPDATE OF pc, p" if lock else ""
    row = await conn.fetchrow(
        f"""
        SELECT pc.id AS code_id, pc.is_active AS code_is_active, pc.usage_limit AS code_usage_limit,
               p.id AS promotion_id, p.name, p.type, p.percentage, p.applies_to_all_items,
               p.is_active AS promo_is_active, p.usage_limit, p.usage_limit_per_customer,
               p.registered_customers_only, p.first_order_only, p.starts_at, p.ends_at
        FROM promotion_codes pc
        JOIN promotions p ON p.id = pc.promotion_id
        WHERE pc.code = $1
        {lock_clause}
        """,
        code,
    )
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

    if row["usage_limit"] is not None:
        used = await conn.fetchval(
            "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
            "WHERE op.promotion_id = $1 AND o.status <> 'cancelled'",
            row["promotion_id"],
        )
        if used >= row["usage_limit"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code usage limit reached")

    if row["code_usage_limit"] is not None:
        used = await conn.fetchval(
            "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
            "WHERE op.promotion_code_id = $1 AND o.status <> 'cancelled'",
            row["code_id"],
        )
        if used >= row["code_usage_limit"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo code usage limit reached")

    if row["usage_limit_per_customer"] is not None:
        if customer is not None:
            used = await conn.fetchval(
                "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
                "WHERE op.promotion_id = $1 AND o.customer_id = $2 AND o.status <> 'cancelled'",
                row["promotion_id"], customer.id,
            )
        else:
            used = await conn.fetchval(
                "SELECT count(*) FROM order_promotions op JOIN orders o ON o.id = op.order_id "
                "WHERE op.promotion_id = $1 AND o.customer_id IS NULL AND o.email = $2 AND o.status <> 'cancelled'",
                row["promotion_id"], email,
            )
        if used >= row["usage_limit_per_customer"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you've already used this promo code")

    if row["first_order_only"]:
        if customer is not None:
            has_order = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM orders WHERE customer_id = $1 AND status IN ('placed', 'completed'))",
                customer.id,
            )
        else:
            has_order = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM orders WHERE customer_id IS NULL AND email = $1 "
                "AND status IN ('placed', 'completed'))",
                email,
            )
        if has_order:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "this promo code is for first orders only")

    discount_amount = 0
    free_shipping = False
    if row["type"] == "percentage":
        discount_amount = round(subtotal * float(row["percentage"]) / 100)
    elif row["type"] == "fixed_amount":
        cv = await conn.fetchrow(
            "SELECT amount_off, min_subtotal FROM promotion_currency_values "
            "WHERE promotion_id = $1 AND currency = $2",
            row["promotion_id"], CURRENCY,
        )
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


async def _resolve_gift_card(conn, code: str, *, lock: bool) -> dict:
    lock_clause = "FOR UPDATE" if lock else ""
    gc = await conn.fetchrow(f'SELECT * FROM gift_cards WHERE code = $1 {lock_clause}', code)
    if gc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown gift card code")
    if gc["status"] != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card is not active")
    if gc["expires_at"] is not None and gc["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card has expired")
    if gc["currency"] != CURRENCY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card currency does not match")
    if gc["balance_amount"] <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gift card has no remaining balance")
    return dict(gc)


# ---------------------------------------------------------------- pricing --

async def _price_cart(
    conn,
    items: list[CheckoutItemIn],
    shipping_method_id: uuid.UUID,
    promo_code: str | None,
    gift_card_code: str | None,
    customer: CurrentCustomer | None,
    email: str | None,
    *,
    lock: bool,
    strict: bool,
) -> dict:
    """The one place order totals are computed -- used identically by the
    real checkout (lock=True, strict=True: any problem aborts) and the
    preview endpoint (lock=False, strict=False: promo/gift-card problems are
    reported back as error strings instead of raising, so the UI can show a
    quote even when a code is invalid; item/shipping problems still raise
    unconditionally in both modes, since there's nothing meaningful to quote
    without a valid cart)."""
    method = await conn.fetchrow(
        """
        SELECT id, code, carrier_code, name, requires_pickup_point, price_amount, free_above_amount
        FROM shipping_methods WHERE id = $1 AND is_active
        """,
        shipping_method_id,
    )
    if method is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or inactive shipping method")

    variant_ids = [i.variant_id for i in items]
    variants = await _load_sellable_variants(conn, variant_ids)
    missing = [str(i.variant_id) for i in items if i.variant_id not in variants]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unavailable variant(s): {missing}")
    unpriced = [str(vid) for vid, v in variants.items() if v["price"] is None]
    if unpriced:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"variant(s) with no price: {unpriced}")

    options = await _load_variant_options(conn, variant_ids)

    lines = []
    subtotal = 0
    for item in items:
        v = variants[item.variant_id]
        unit_price = v["price"]
        line_subtotal = unit_price * item.quantity
        opts = options.get(item.variant_id, [])
        lines.append({
            "variant_id": item.variant_id, "product_id": v["product_id"], "sku": v["sku"],
            "product_name": v["product_name"], "image_key": v["image_key"], "tax_class_id": v["tax_class_id"],
            "variant_label": " / ".join(o["label"] for o in opts) or None,
            "attributes": opts, "quantity": item.quantity, "unit_price": unit_price,
            "subtotal": line_subtotal,
        })
        subtotal += line_subtotal

    stock_reserved = await _check_and_reserve_stock(conn, lines, lock=lock)
    for line in lines:
        line["stock_reserved"] = stock_reserved.get(line["variant_id"], False)

    promo = None
    promo_error = None
    if promo_code:
        try:
            promo = await _resolve_promo(conn, promo_code, customer, email, subtotal, lock=lock)
        except HTTPException as e:
            if strict:
                raise
            promo_error = e.detail

    discount_amount = promo["discount_amount"] if promo else 0
    free_shipping_promo = promo["free_shipping"] if promo else False

    # Distribute the product discount across lines (remainder-corrected so
    # the sum is always exactly discount_amount), then price tax off the
    # POST-discount line total -- a discounted item has proportionally less
    # embedded VAT, same as a real retail receipt.
    line_discounts = _distribute_amount([l["subtotal"] for l in lines], discount_amount) if lines else []
    today = date.today()
    tax_rate_cache: dict[uuid.UUID, float] = {}
    tax_amount = 0
    for line, d in zip(lines, line_discounts):
        line["discount_amount"] = d
        line["total_amount"] = line["subtotal"] - d
        tcid = line["tax_class_id"]
        if tcid and tcid not in tax_rate_cache:
            rate = await conn.fetchval(
                """
                SELECT rate FROM tax_rates
                WHERE tax_class_id = $1 AND country_code = $2
                  AND valid_from <= $3 AND (valid_to IS NULL OR valid_to > $3)
                ORDER BY valid_from DESC LIMIT 1
                """,
                tcid, DEFAULT_TAX_COUNTRY, today,
            )
            tax_rate_cache[tcid] = float(rate) if rate is not None else 0.0
        rate = tax_rate_cache.get(tcid, 0.0)
        line["tax_rate"] = rate
        line["tax_amount"] = round(line["total_amount"] - line["total_amount"] / (1 + rate)) if rate else 0
        tax_amount += line["tax_amount"]

    free_above = method["free_above_amount"]
    threshold_met = free_above is not None and subtotal >= free_above
    shipping_amount = 0 if (free_shipping_promo or threshold_met) else method["price_amount"]

    total_amount = subtotal - discount_amount + shipping_amount

    gift_card = None
    gift_card_error = None
    gift_card_amount = 0
    if gift_card_code:
        try:
            gift_card = await _resolve_gift_card(conn, gift_card_code, lock=lock)
            gift_card_amount = min(gift_card["balance_amount"], total_amount)
        except HTTPException as e:
            if strict:
                raise
            gift_card_error = e.detail

    amount_due = total_amount - gift_card_amount

    return {
        "lines": lines, "subtotal": subtotal, "discount_amount": discount_amount, "promo": promo,
        "promo_error": promo_error, "shipping_amount": shipping_amount, "shipping_method": dict(method),
        "tax_amount": tax_amount, "total_amount": total_amount, "gift_card": gift_card,
        "gift_card_error": gift_card_error, "gift_card_amount": gift_card_amount, "amount_due": amount_due,
    }


@router.post("/checkout/preview", response_model=PricingPreviewOut)
async def preview_checkout(
    body: PricingPreviewIn, customer: CurrentCustomer | None = Depends(get_current_customer_optional)
):
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")
    async with pool().acquire() as conn:
        pricing = await _price_cart(
            conn, body.items, body.shipping_method_id, body.promo_code, body.gift_card_code,
            customer, customer.email if customer else None, lock=False, strict=False,
        )
    return PricingPreviewOut(
        subtotal_amount=pricing["subtotal"], discount_amount=pricing["discount_amount"],
        shipping_amount=pricing["shipping_amount"], tax_amount=pricing["tax_amount"],
        total_amount=pricing["total_amount"], gift_card_amount=pricing["gift_card_amount"],
        amount_due=pricing["amount_due"],
        promo_name=pricing["promo"]["name"] if pricing["promo"] else None,
        promo_error=pricing["promo_error"], gift_card_error=pricing["gift_card_error"],
    )


# -------------------------------------------------------- order lifecycle --

async def _sweep_stale_pending_orders(conn) -> None:
    """No scheduler exists in this project, so cleanup is opportunistic:
    every new checkout attempt first cancels any pending order old enough
    that its Stripe session must already be expired, refunding whatever
    gift card hold it had. STALE_PENDING_MINUTES is kept comfortably above
    STRIPE_SESSION_EXPIRES_MINUTES so this never cancels an order that's
    still genuinely payable."""
    stale = await conn.fetch(
        f"SELECT id FROM orders WHERE status = 'pending' "
        f"AND created_at < now() - interval '{STALE_PENDING_MINUTES} minutes'"
    )
    for row in stale:
        await _cancel_pending_order(conn, row["id"], "expired")


async def _cancel_pending_order(conn, order_id: uuid.UUID, reason: str) -> None:
    updated = await conn.fetchval(
        """
        UPDATE orders SET status = 'cancelled', cancelled_at = now(), cancel_reason = $2
        WHERE id = $1 AND status = 'pending'
        RETURNING id
        """,
        order_id, reason,
    )
    if updated is None:
        return
    gc_tx = await conn.fetchrow(
        "SELECT gift_card_id, -amount_delta AS amount FROM gift_card_transactions "
        "WHERE order_id = $1 AND reason = 'redeemed'",
        order_id,
    )
    if gc_tx:
        await conn.execute(
            "INSERT INTO gift_card_transactions (gift_card_id, amount_delta, reason, order_id, note) "
            "VALUES ($1, $2, 'refunded', $3, $4)",
            gc_tx["gift_card_id"], gc_tx["amount"], order_id, f"checkout {reason}",
        )
    # Restock exactly the lines that actually had stock decremented at
    # order-creation time (order_lines.stock_reserved) -- not re-derived
    # from the variant's current track_inventory/allow_backorder, which may
    # have changed since.
    reserved_lines = await conn.fetch(
        "SELECT variant_id, quantity FROM order_lines WHERE order_id = $1 AND stock_reserved",
        order_id,
    )
    for line in reserved_lines:
        await conn.execute(
            "UPDATE product_variants SET quantity_on_hand = quantity_on_hand + $1 WHERE id = $2",
            line["quantity"], line["variant_id"],
        )


async def _create_pending_order(
    conn, body: CheckoutSessionIn, customer: CurrentCustomer | None
) -> dict:
    email = customer.email if customer else body.email
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email is required")

    method_row = await conn.fetchrow(
        "SELECT requires_pickup_point FROM shipping_methods WHERE id = $1 AND is_active",
        body.shipping_method_id,
    )
    if method_row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or inactive shipping method")
    if method_row["requires_pickup_point"] and body.pickup_point is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pickup_point is required for this shipping method")
    if not method_row["requires_pickup_point"] and body.address is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "address is required for this shipping method")

    pricing = await _price_cart(
        conn, body.items, body.shipping_method_id, body.promo_code, body.gift_card_code,
        customer, email, lock=True, strict=True,
    )
    lines = pricing["lines"]
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")

    pickup_point = (
        {
            "point_name": body.pickup_point.point_name, "point_address": body.pickup_point.point_address,
            "first_name": body.pickup_point.first_name, "last_name": body.pickup_point.last_name,
            "phone": body.pickup_point.phone,
        }
        if body.pickup_point else None
    )
    method = pricing["shipping_method"]

    order_row = await conn.fetchrow(
        """
        INSERT INTO orders
          (customer_id, email, phone, locale, currency, status, payment_status,
           prices_include_tax, subtotal_amount, discount_amount, shipping_amount, tax_amount, total_amount,
           shipping_method_id, shipping_method_code, shipping_method_name, shipping_carrier_code, pickup_point)
        VALUES ($1, $2, $3, $4, $5, 'pending', 'unpaid', true, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15)
        RETURNING id, number
        """,
        customer.id if customer else None, email,
        body.pickup_point.phone if body.pickup_point else (body.address.phone if body.address else None),
        LOCALE, CURRENCY, pricing["subtotal"], pricing["discount_amount"], pricing["shipping_amount"],
        pricing["tax_amount"], pricing["total_amount"], method["id"], method["code"], method["name"],
        method["carrier_code"], pickup_point,
    )
    order_id = order_row["id"]

    for line in lines:
        await conn.execute(
            """
            INSERT INTO order_lines
              (order_id, product_id, variant_id, sku, product_name, variant_label,
               attributes, quantity, unit_price_amount, subtotal_amount, discount_amount,
               tax_rate, tax_amount, total_amount, stock_reserved)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            """,
            order_id, line["product_id"], line["variant_id"], line["sku"], line["product_name"],
            line["variant_label"], line["attributes"], line["quantity"], line["unit_price"],
            line["subtotal"], line["discount_amount"], line["tax_rate"], line["tax_amount"],
            line["total_amount"], line["stock_reserved"],
        )

    if body.address:
        a = body.address
        await conn.execute(
            """
            INSERT INTO order_addresses
              (order_id, kind, first_name, last_name, company, line1, line2, city,
               region, postal_code, country_code, phone)
            VALUES ($1, 'shipping', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            order_id, a.first_name, a.last_name, a.company, a.line1, a.line2, a.city,
            a.region, a.postal_code, a.country_code, a.phone,
        )

    if pricing["promo"]:
        await conn.execute(
            "INSERT INTO order_promotions (order_id, promotion_id, promotion_code_id, discount_amount) "
            "VALUES ($1, $2, $3, $4)",
            order_id, pricing["promo"]["promotion_id"], pricing["promo"]["code_id"], pricing["discount_amount"],
        )

    if pricing["gift_card"] and pricing["gift_card_amount"] > 0:
        await conn.execute(
            "INSERT INTO gift_card_transactions (gift_card_id, amount_delta, reason, order_id, note) "
            "VALUES ($1, $2, 'redeemed', $3, 'checkout')",
            pricing["gift_card"]["id"], -pricing["gift_card_amount"], order_id,
        )

    return {
        "id": order_id, "number": order_row["number"], "lines": lines,
        "discount_amount": pricing["discount_amount"], "shipping_amount": pricing["shipping_amount"],
        "shipping_method_name": method["name"], "total_amount": pricing["total_amount"],
        "gift_card_amount": pricing["gift_card_amount"], "amount_due": pricing["amount_due"],
    }


async def _finalize_zero_dollar_order(conn, order_id: uuid.UUID) -> None:
    """A gift card covered the whole order -- Stripe is never involved."""
    updated = await conn.fetchval(
        "UPDATE orders SET status = 'placed', payment_status = 'paid', placed_at = now() "
        "WHERE id = $1 AND status = 'pending' RETURNING id",
        order_id,
    )
    if updated is None:
        return
    await _record_gift_card_payment(conn, order_id)


async def _record_gift_card_payment(conn, order_id: uuid.UUID) -> None:
    """The gift card balance was already reserved at order-creation time
    (see _create_pending_order); this just adds the matching `payments` row
    once the order is confirmed paid, so SUM(payments.amount) for an order
    always equals its total_amount regardless of how many instruments paid
    for it -- a plain reporting row, no additional ledger effect."""
    gc_tx = await conn.fetchrow(
        "SELECT gift_card_id, -amount_delta AS amount FROM gift_card_transactions "
        "WHERE order_id = $1 AND reason = 'redeemed'",
        order_id,
    )
    if gc_tx is None:
        return
    await conn.execute(
        """
        INSERT INTO payments
          (order_id, currency, provider, provider_payment_id, method_type, status,
           amount, amount_captured, idempotency_key)
        VALUES ($1, $2, 'gift_card', $3, 'gift_card', 'succeeded', $4, $4, $3)
        ON CONFLICT (provider, provider_payment_id) DO NOTHING
        """,
        order_id, CURRENCY, f"giftcard:{order_id}", gc_tx["amount"],
    )


@router.post("/checkout/session", response_model=CheckoutSessionOut)
async def create_checkout_session(
    body: CheckoutSessionIn,
    customer: CurrentCustomer | None = Depends(get_current_customer_optional),
):
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")

    # Deliberately its own transaction, committed before order creation even
    # starts: if the order below fails validation and rolls back, that must
    # not take this cleanup down with it -- a bad request on one checkout
    # attempt shouldn't silently un-sweep stale orders from unrelated ones.
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _sweep_stale_pending_orders(conn)

    async with pool().acquire() as conn:
        async with conn.transaction():
            order = await _create_pending_order(conn, body, customer)

    if order["amount_due"] <= 0:
        async with pool().acquire() as conn:
            async with conn.transaction():
                await _finalize_zero_dollar_order(conn, order["id"])
        return CheckoutSessionOut(url=None, order_number=order["number"])

    # Reduce each product line by its share of the gift card's coverage only
    # when a gift card is actually partially covering the order -- the
    # common no-gift-card path keeps the exact per-unit price/quantity
    # Stripe already showed before this feature existed.
    gift_card_amount = order["gift_card_amount"]
    line_items = []
    if gift_card_amount > 0:
        weights = [l["total_amount"] for l in order["lines"]] + (
            [order["shipping_amount"]] if order["shipping_amount"] > 0 else []
        )
        reductions = _distribute_amount(weights, gift_card_amount)
        line_reductions = reductions[: len(order["lines"])]
        shipping_reduction = reductions[len(order["lines"])] if order["shipping_amount"] > 0 else 0
    else:
        line_reductions = [0] * len(order["lines"])
        shipping_reduction = 0

    for line, reduction in zip(order["lines"], line_reductions):
        final_amount = line["total_amount"] - reduction
        if final_amount <= 0:
            continue  # fully covered by the gift card -- nothing for Stripe to charge for this line
        images = [public_url(line["image_key"])] if line["image_key"] else []
        if reduction > 0:
            # Collapsed to an exact total rather than unit_amount*quantity:
            # a discount/gift-card reduction split across multiple units
            # doesn't necessarily divide evenly, and this guarantees the sum
            # Stripe charges is exactly right rather than off by a cent.
            label = line["product_name"] + (f" x{line['quantity']}" if line["quantity"] > 1 else "")
            line_items.append({
                "price_data": {"currency": CURRENCY.lower(), "unit_amount": final_amount,
                                "product_data": {"name": label, "images": images}},
                "quantity": 1,
            })
        else:
            line_items.append({
                "price_data": {"currency": CURRENCY.lower(), "unit_amount": line["unit_price"],
                                "product_data": {"name": line["product_name"], "images": images}},
                "quantity": line["quantity"],
            })

    final_shipping = order["shipping_amount"] - shipping_reduction
    if final_shipping > 0:
        line_items.append({
            "price_data": {"currency": CURRENCY.lower(), "unit_amount": final_shipping,
                            "product_data": {"name": order["shipping_method_name"]}},
            "quantity": 1,
        })

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            customer_email=customer.email if customer else body.email,
            client_reference_id=str(order["id"]),
            success_url=f"{body.success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=body.cancel_url,
            expires_at=int(time.time()) + STRIPE_SESSION_EXPIRES_MINUTES * 60,
        )
    except stripe.StripeError as e:
        # Nothing for the customer to pay for yet -- cancel (not delete) so
        # any gift card hold gets refunded and the attempt stays visible in
        # the admin panel rather than silently disappearing.
        async with pool().acquire() as conn:
            async with conn.transaction():
                await _cancel_pending_order(conn, order["id"], "checkout_session_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Stripe error: {getattr(e, 'user_message', None) or str(e)}",
        )

    await pool().execute(
        "UPDATE orders SET metadata = $2 WHERE id = $1",
        order["id"], {"stripe_checkout_session_id": session.id},
    )

    return CheckoutSessionOut(url=session.url)


async def _mark_order_paid(conn, order_id: uuid.UUID, session: dict[str, Any]) -> bool:
    """Idempotent: the UPDATE only matches a still-'pending' order, so a
    retried webhook (or the success-page fallback below racing the webhook)
    is a harmless no-op the second time. Returns True if this call is the one
    that actually marked it paid."""
    updated = await conn.fetchval(
        """
        UPDATE orders SET status = 'placed', payment_status = 'paid', placed_at = now()
        WHERE id = $1 AND status = 'pending'
        RETURNING id
        """,
        order_id,
    )
    if updated is None:
        return False

    # Shipping address / pickup point were already persisted at order-creation
    # time (our own checkout page collected them) -- nothing to fill in from
    # Stripe here, it only ever handled payment.
    await _record_gift_card_payment(conn, order_id)

    payment_intent_id = session["payment_intent"]
    pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["payment_method"])
    pm = pi.payment_method
    method_type = pm.type if pm else None
    card_brand = pm.card.brand if pm and pm.type == "card" and pm.card else None
    card_last4 = pm.card.last4 if pm and pm.type == "card" and pm.card else None
    # pi.amount is what Stripe actually confirms it charged -- authoritative
    # over recomputing total_amount - gift_card_amount ourselves again here.
    stripe_amount = pi.amount

    await conn.execute(
        """
        INSERT INTO payments
          (order_id, currency, provider, provider_payment_id, method_type, status,
           amount, amount_captured, card_brand, card_last4, idempotency_key)
        VALUES ($1,$2,'stripe',$3,$4,'succeeded',$5,$5,$6,$7,$3)
        ON CONFLICT (provider, provider_payment_id) DO NOTHING
        """,
        order_id, CURRENCY, payment_intent_id, method_type, stripe_amount, card_brand, card_last4,
    )
    return True


@router.post("/checkout/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook signature")

    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        # stripe-python v16's StripeObject no longer subclasses dict -- .get()
        # raises AttributeError on it (only subscript/.to_dict() still work),
        # so convert once here and let everything downstream use plain .get().
        session = event["data"]["object"].to_dict()
        order_id = session.get("client_reference_id")
        if order_id and session.get("payment_status") == "paid":
            async with pool().acquire() as conn:
                async with conn.transaction():
                    await _mark_order_paid(conn, uuid.UUID(order_id), session)

    return {"received": True}


@router.get("/checkout/session/{session_id}", response_model=CheckoutStatusOut)
async def get_checkout_status(session_id: str):
    row = await pool().fetchrow(
        """
        SELECT id, number, status, total_amount FROM orders
        WHERE metadata ->> 'stripe_checkout_session_id' = $1
        """,
        session_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "checkout session not found")
    if row["status"] != "pending":
        return CheckoutStatusOut(status="paid", order_number=row["number"], total_amount=row["total_amount"])

    # Still 'pending' in our DB -- either the customer hasn't paid yet, or
    # they have and the webhook just hasn't landed. Ask Stripe directly
    # rather than keep the customer guessing, and self-heal here if the
    # webhook is the thing that's late or missing entirely.
    try:
        session = stripe.checkout.Session.retrieve(session_id).to_dict()
    except stripe.StripeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")

    if session.get("payment_status") == "paid":
        async with pool().acquire() as conn:
            async with conn.transaction():
                await _mark_order_paid(conn, row["id"], session)
        updated = await pool().fetchrow("SELECT number, total_amount FROM orders WHERE id = $1", row["id"])
        return CheckoutStatusOut(status="paid", order_number=updated["number"], total_amount=updated["total_amount"])

    return CheckoutStatusOut(status="pending")
