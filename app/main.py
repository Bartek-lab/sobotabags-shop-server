from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import db
from app.config import settings
from app.rate_limit import limiter
from app.routers import ALL_ROUTERS


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(title="Storefront API (api-shop)", lifespan=lifespan)

# Per-route limits live on /checkout/session, /checkout/preview, and
# /contact (see their routers) -- this just wires the shared Limiter and
# exception handler into the app. In-memory storage is fine: each Render
# free-tier service is a single process/instance.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

for r in ALL_ROUTERS:
    app.include_router(r)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
