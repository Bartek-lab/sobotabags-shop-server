import json

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=10,
        init=_init_connection,
        # Supabase's pooler (even in session mode) sits in front of PgBouncer;
        # statement_cache_size=0 avoids asyncpg trying to reuse prepared
        # statements across what PgBouncer may treat as different backend
        # sessions. Cheap insurance for a POC-scale app.
        statement_cache_size=0,
    )


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized -- did the app startup lifespan run?")
    return _pool
