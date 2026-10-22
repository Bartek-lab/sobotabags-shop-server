"""The one place order totals are computed -- used identically by the real
checkout (lock=True, strict=True: any problem aborts) and the preview
endpoint (lock=False, strict=False: promo/gift-card problems are reported
back as error strings instead of raising, so the UI can show a quote even
when a code is invalid; item/shipping/stock problems still raise
unconditionally in both modes, since there's nothing meaningful to quote
without a valid, fulfillable cart)."""

import uuid
from datetime import date

from fastapi import HTTPException, status

from app.repositories import pricing_repository
from app.schemas.checkout import CheckoutItemIn
from app.schemas.customer import CurrentCustomer
from app.services import gift_card_service, promotion_service, stock_service

DEFAULT_TAX_COUNTRY = "PL"  # matches the admin side's Poland-only tax config


def distribute_amount(weights: list[int], total_to_distribute: int) -> list[int]:
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


async def price_cart(
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
    method = await pricing_repository.get_active_shipping_method(conn, shipping_method_id)
    if method is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or inactive shipping method")

    variant_ids = [i.variant_id for i in items]
    variants = await pricing_repository.load_sellable_variants(conn, variant_ids)
    missing = [str(i.variant_id) for i in items if i.variant_id not in variants]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unavailable variant(s): {missing}")
    unpriced = [str(vid) for vid, v in variants.items() if v["price"] is None]
    if unpriced:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"variant(s) with no price: {unpriced}")

    options = await pricing_repository.load_variant_options(conn, variant_ids)

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

    stock_reserved = await stock_service.check_and_reserve_stock(conn, lines, lock=lock)
    for line in lines:
        line["stock_reserved"] = stock_reserved.get(line["variant_id"], False)

    promo = None
    promo_error = None
    if promo_code:
        try:
            promo = await promotion_service.resolve_promo(conn, promo_code, customer, email, subtotal, lock=lock)
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
    line_discounts = distribute_amount([l["subtotal"] for l in lines], discount_amount) if lines else []
    today = date.today()
    tax_rate_cache: dict[uuid.UUID, float] = {}
    tax_amount = 0
    for line, d in zip(lines, line_discounts):
        line["discount_amount"] = d
        line["total_amount"] = line["subtotal"] - d
        tcid = line["tax_class_id"]
        if tcid and tcid not in tax_rate_cache:
            rate = await pricing_repository.get_tax_rate(conn, tcid, DEFAULT_TAX_COUNTRY, today)
            tax_rate_cache[tcid] = rate if rate is not None else 0.0
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
            gift_card = await gift_card_service.resolve_gift_card(conn, gift_card_code, lock=lock)
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
