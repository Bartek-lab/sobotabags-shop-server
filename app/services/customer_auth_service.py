"""Verifies a Supabase session JWT and resolves it to our customers row.

Verifies against Supabase's JWKS endpoint (asymmetric, rotatable keys), not
the legacy shared HS256 "JWT Secret" -- confirmed via Supabase's current docs
that the legacy secret is deprecated-but-still-working, while JWKS is the
recommended path for new backends. PyJWKClient caches fetched keys itself;
get_signing_key_from_jwt does a blocking HTTP call on a cache miss, so it's
run via asyncio.to_thread rather than blocking the event loop.
"""

import asyncio

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.config import settings
from app.db import pool
from app.repositories import customer_repository
from app.schemas.customer import CurrentCustomer

_jwks_client = PyJWKClient(settings.supabase_jwks_url)


async def verify_token(token: str) -> CurrentCustomer:
    try:
        signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
        )
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    row = await customer_repository.find_by_id(pool(), payload["sub"])
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Customer not found")
    return CurrentCustomer(**dict(row))
