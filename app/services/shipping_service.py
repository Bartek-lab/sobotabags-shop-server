"""No business rules yet beyond "active methods, in order" -- kept as a real
service (not router -> repository directly) for layering consistency, and
because a future rule (e.g. hide a method for certain postal codes) has an
obvious home already."""

from app.db import pool
from app.repositories import shipping_repository
from app.schemas.shipping import ShippingMethodOut


async def list_active_methods() -> list[ShippingMethodOut]:
    rows = await shipping_repository.list_active_shipping_methods(pool())
    return [ShippingMethodOut(**dict(r)) for r in rows]
