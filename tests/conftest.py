"""Shared fixtures for shop-api's test suite.

Mocking strategy: every repository module exposes plain importable async
functions (app.repositories.X.func) -- tests monkeypatch those directly
rather than faking asyncpg's Record/Pool/Connection protocol. Where a
service or router acquires a connection via `pool().acquire()` purely to
pass it through to already-mocked repository calls (it's never used for a
real query once mocked), `fake_conn`/`fake_pool` below provide a minimal
stand-in supporting `.acquire()` / `.transaction()` as no-op async context
managers.

No real DB, no real Stripe, no real Resend anywhere in this suite.
"""

import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

# Settings are required fields with no defaults (database_url, supabase_url,
# stripe keys, resend key) -- must exist before `from app.config import
# settings` (triggered transitively by importing app.main) runs anywhere.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_fake")
os.environ.setdefault("RESEND_API_KEY", "re_fake")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import get_current_customer, get_current_customer_optional  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.customer import CurrentCustomer  # noqa: E402

FAKE_CUSTOMER = CurrentCustomer(
    id="11111111-1111-1111-1111-111111111111",
    email="customer@example.com",
    first_name="Jan",
    last_name="Kowalski",
)


class FakeConn:
    """Stands in for an acquired asyncpg connection. `.transaction()` is a
    no-op async context manager -- fine as long as every query the code
    would run on this connection is itself mocked at the repository-function
    level, so nothing ever actually touches this object's (nonexistent)
    query methods."""

    def transaction(self):
        return _noop_cm()

    async def fetch(self, *a, **k):
        raise AssertionError("FakeConn.fetch called directly -- mock the repository function instead")

    async def fetchrow(self, *a, **k):
        raise AssertionError("FakeConn.fetchrow called directly -- mock the repository function instead")

    async def fetchval(self, *a, **k):
        raise AssertionError("FakeConn.fetchval called directly -- mock the repository function instead")

    async def execute(self, *a, **k):
        raise AssertionError("FakeConn.execute called directly -- mock the repository function instead")


@asynccontextmanager
async def _noop_cm():
    yield


class FakePool:
    def acquire(self):
        return _acquire_cm()

    async def fetch(self, *a, **k):
        raise AssertionError("FakePool.fetch called directly -- mock the repository function instead")

    async def fetchrow(self, *a, **k):
        raise AssertionError("FakePool.fetchrow called directly -- mock the repository function instead")

    async def fetchval(self, *a, **k):
        raise AssertionError("FakePool.fetchval called directly -- mock the repository function instead")

    async def execute(self, *a, **k):
        raise AssertionError("FakePool.execute called directly -- mock the repository function instead")


@asynccontextmanager
async def _acquire_cm():
    yield FakeConn()


@pytest.fixture
def fake_pool(monkeypatch):
    """Patches app.db._pool directly -- since every module does `from
    app.db import pool` and calls `pool()`, and that function reads the
    module-level `_pool` global at CALL time (not import time), this one
    patch makes every caller across the whole app see the fake pool,
    regardless of which module imported `pool`."""
    import app.db as db_module

    pool_instance = FakePool()
    monkeypatch.setattr(db_module, "_pool", pool_instance)
    return pool_instance


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """TestClient requests all share one fake remote address -- without
    resetting the limiter's in-memory storage between tests, rate-limit
    tests in one module would spuriously trip unrelated tests in another."""
    app.state.limiter.reset()
    yield


# Deliberately NOT used as a context manager (`with TestClient(app) as c`)
# anywhere in this file -- that form runs the app's lifespan, which calls
# db.connect() and tries to open a real asyncpg pool against DATABASE_URL.
# This whole suite mocks the repository layer and never needs a real pool
# (see fake_pool below for the handful of tests that need `pool()` to at
# least not raise), so skipping lifespan avoids a real connection attempt.


@pytest.fixture
def client():
    """Plain TestClient, no auth override -- used for public endpoints and
    for explicitly testing the 401-when-unauthenticated paths."""
    return TestClient(app)


@pytest.fixture
def authed_client():
    """TestClient with get_current_customer/get_current_customer_optional
    overridden to a fixed fake customer -- used for endpoints that require
    or accept a logged-in customer, where the JWKS verification path itself
    isn't what's under test (that's covered separately in test_auth.py)."""
    app.dependency_overrides[get_current_customer] = lambda: FAKE_CUSTOMER
    app.dependency_overrides[get_current_customer_optional] = lambda: FAKE_CUSTOMER
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_customer, None)
    app.dependency_overrides.pop(get_current_customer_optional, None)


@pytest.fixture
def guest_client():
    """TestClient where get_current_customer_optional resolves to None (a
    guest checkout) -- get_current_customer is left un-overridden since
    guest-only endpoints never depend on it."""
    app.dependency_overrides[get_current_customer_optional] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_customer_optional, None)


def make_async_mock(return_value=None, side_effect=None):
    return AsyncMock(return_value=return_value, side_effect=side_effect)
