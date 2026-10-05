from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, status


@asynccontextmanager
async def translate_db_errors():
    """Every invariant that matters in this schema is enforced by a Postgres
    trigger or constraint, not application code -- which means a rejected
    write surfaces as an asyncpg.PostgresError, not a validation error FastAPI
    already knows how to render. Without this, the storefront would show a
    bare 500 with no message for things like an invalid phone number."""
    try:
        yield
    except asyncpg.PostgresError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
