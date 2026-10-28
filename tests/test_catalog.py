import uuid
from unittest.mock import AsyncMock

from app.repositories import catalog_repository

PRODUCT_ID = uuid.uuid4()
VARIANT_ID = uuid.uuid4()


def test_list_categories(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        catalog_repository,
        "list_categories",
        AsyncMock(
            return_value=[
                {"id": uuid.uuid4(), "code": "bags", "parent_id": None, "name": "Torby", "slug": "bags"},
            ]
        ),
    )
    resp = client.get("/categories")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["slug"] == "bags"


def _product_list_row(**overrides):
    row = {
        "id": PRODUCT_ID, "slug": "shopper-bea", "name": "Shopper Bea",
        "short_description": "Skórzana torba", "price_from": 45000,
        "image_key": "products/1.jpg", "category_slug": "bags", "category_name": "Torby",
    }
    row.update(overrides)
    return row


def test_list_products_sets_total_count_header(client, fake_pool, monkeypatch):
    monkeypatch.setattr(catalog_repository, "list_products", AsyncMock(return_value=[_product_list_row()]))
    monkeypatch.setattr(catalog_repository, "count_products", AsyncMock(return_value=1))
    resp = client.get("/products")
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    assert resp.json()[0]["slug"] == "shopper-bea"


def test_list_products_rejects_bad_sort(client, fake_pool):
    resp = client.get("/products?sort=random")
    assert resp.status_code == 422


def test_list_products_passes_category_filter(client, fake_pool, monkeypatch):
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(catalog_repository, "list_products", list_mock)
    monkeypatch.setattr(catalog_repository, "count_products", AsyncMock(return_value=0))
    client.get("/products?category=handbags")
    _, kwargs = list_mock.call_args
    assert kwargs["category"] == "handbags"


def test_get_product_not_found(client, fake_pool, monkeypatch):
    monkeypatch.setattr(catalog_repository, "get_product_by_slug", AsyncMock(return_value=None))
    resp = client.get("/products/does-not-exist")
    assert resp.status_code == 404


def test_get_product_happy_path_computes_availability_and_low_stock(client, fake_pool, monkeypatch):
    monkeypatch.setattr(
        catalog_repository, "get_product_by_slug",
        AsyncMock(return_value={
            "id": PRODUCT_ID, "slug": "shopper-bea", "name": "Shopper Bea",
            "short_description": "short", "description": "long",
        }),
    )
    monkeypatch.setattr(catalog_repository, "list_product_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog_repository, "get_primary_category_slug", AsyncMock(return_value="bags"))
    monkeypatch.setattr(
        catalog_repository, "list_variants_for_display",
        AsyncMock(return_value=[
            # in stock, well above low-stock threshold
            {"id": VARIANT_ID, "sku": "SKU-1", "amount": 45000, "compare_at_amount": None,
             "quantity_on_hand": 10, "allow_backorder": False, "track_inventory": True},
            # low stock
            {"id": uuid.uuid4(), "sku": "SKU-2", "amount": 45000, "compare_at_amount": None,
             "quantity_on_hand": 2, "allow_backorder": False, "track_inventory": True},
            # out of stock, no backorder -> unavailable
            {"id": uuid.uuid4(), "sku": "SKU-3", "amount": 45000, "compare_at_amount": None,
             "quantity_on_hand": 0, "allow_backorder": False, "track_inventory": True},
            # out of stock but backorder allowed -> still available
            {"id": uuid.uuid4(), "sku": "SKU-4", "amount": 45000, "compare_at_amount": None,
             "quantity_on_hand": 0, "allow_backorder": True, "track_inventory": True},
            # inventory not tracked at all -> always available
            {"id": uuid.uuid4(), "sku": "SKU-5", "amount": 45000, "compare_at_amount": None,
             "quantity_on_hand": 0, "allow_backorder": False, "track_inventory": False},
        ]),
    )
    monkeypatch.setattr(catalog_repository, "list_variant_options_for_display", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog_repository, "list_product_attribute_values", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog_repository, "list_product_attribute_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog_repository, "list_related_products", AsyncMock(return_value=[]))

    resp = client.get("/products/shopper-bea")
    assert resp.status_code == 200
    variants = resp.json()["variants"]
    by_sku = {v["sku"]: v for v in variants}
    assert by_sku["SKU-1"]["available"] is True and by_sku["SKU-1"]["low_stock"] is False
    assert by_sku["SKU-2"]["available"] is True and by_sku["SKU-2"]["low_stock"] is True
    assert by_sku["SKU-3"]["available"] is False and by_sku["SKU-3"]["low_stock"] is False
    assert by_sku["SKU-4"]["available"] is True and by_sku["SKU-4"]["low_stock"] is False
    assert by_sku["SKU-5"]["available"] is True and by_sku["SKU-5"]["low_stock"] is False
