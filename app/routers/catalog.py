"""Public, unauthenticated storefront catalog endpoints. Routing only --
request parsing and response_model wiring; the actual queries and DTO
shaping live in app/repositories/catalog_repository.py and
app/services/catalog_service.py.
"""

from fastapi import APIRouter, Query, Response

from app.schemas.catalog import Category, ProductDetail, ProductListItem
from app.services import catalog_service

router = APIRouter(tags=["catalog"])


@router.get("/categories", response_model=list[Category])
async def list_categories():
    return await catalog_service.list_categories()


@router.get("/products", response_model=list[ProductListItem])
async def list_products(
    response: Response,
    category: str | None = Query(None, description="category slug"),
    sort: str = Query("newest", pattern="^(newest|price-asc|price-desc)$"),
    start: int = Query(0, ge=0),
    end: int = Query(24, ge=1),
):
    items, total = await catalog_service.list_products(category=category, sort=sort, start=start, end=end)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/products/{slug}", response_model=ProductDetail)
async def get_product(slug: str):
    return await catalog_service.get_product_detail(slug)
