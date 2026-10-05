"""FastAPI Depends() adapters for customer identity. There is no login/signup
endpoint here -- Supabase Auth is reached directly by the Next.js storefront
(browser), never through this API; the actual token verification logic lives
in services/customer_auth_service.py, this module just exposes it in the
shape FastAPI's dependency injection expects.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.customer import CurrentCustomer
from app.services import customer_auth_service

bearer_scheme = HTTPBearer(auto_error=False)

__all__ = ["CurrentCustomer", "get_current_customer", "get_current_customer_optional"]


async def get_current_customer(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentCustomer:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return await customer_auth_service.verify_token(creds.credentials)


async def get_current_customer_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentCustomer | None:
    """Checkout has to work for guests too -- unlike `get_current_customer`,
    a missing/invalid token here just means 'anonymous', not a 401. Stripe
    Checkout collects the email itself in that case."""
    if creds is None:
        return None
    try:
        return await customer_auth_service.verify_token(creds.credentials)
    except HTTPException:
        return None
