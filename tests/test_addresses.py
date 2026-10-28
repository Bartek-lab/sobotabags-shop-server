import uuid
from unittest.mock import AsyncMock

from app.repositories import address_repository

ADDRESS_ID = uuid.uuid4()


def _address_in(**overrides):
    body = {
        "first_name": "Jan", "last_name": "Kowalski", "line1": "Testowa 1",
        "city": "Warszawa", "postal_code": "00-001", "country_code": "PL",
    }
    body.update(overrides)
    return body


def _address_row(**overrides):
    row = {
        "id": ADDRESS_ID, "label": None, "first_name": "Jan", "last_name": "Kowalski",
        "company": None, "vat_id": None, "line1": "Testowa 1", "line2": None, "city": "Warszawa",
        "region": None, "postal_code": "00-001", "country_code": "PL", "phone": None,
        "is_default_shipping": False, "is_default_billing": False,
    }
    row.update(overrides)
    return row


def test_list_addresses_requires_auth(client):
    assert client.get("/me/addresses").status_code == 401


def test_list_addresses_happy_path(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(address_repository, "list_for_customer", AsyncMock(return_value=[_address_row()]))
    resp = authed_client.get("/me/addresses")
    assert resp.status_code == 200
    assert resp.json()[0]["city"] == "Warszawa"


def test_create_address_requires_auth(client):
    assert client.post("/me/addresses", json=_address_in()).status_code == 401


def test_create_address_happy_path_clears_existing_defaults_first(authed_client, fake_pool, monkeypatch):
    clear_mock = AsyncMock()
    insert_mock = AsyncMock(return_value=_address_row(is_default_shipping=True))
    monkeypatch.setattr(address_repository, "clear_defaults", clear_mock)
    monkeypatch.setattr(address_repository, "insert", insert_mock)

    resp = authed_client.post("/me/addresses", json=_address_in(is_default_shipping=True))
    assert resp.status_code == 201
    assert resp.json()["is_default_shipping"] is True
    clear_mock.assert_awaited_once()
    insert_mock.assert_awaited_once()


def test_update_address_not_found(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(address_repository, "clear_defaults", AsyncMock())
    monkeypatch.setattr(address_repository, "update", AsyncMock(return_value=None))
    resp = authed_client.put(f"/me/addresses/{ADDRESS_ID}", json=_address_in())
    assert resp.status_code == 404


def test_update_address_happy_path(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(address_repository, "clear_defaults", AsyncMock())
    monkeypatch.setattr(address_repository, "update", AsyncMock(return_value=_address_row(city="Kraków")))
    resp = authed_client.put(f"/me/addresses/{ADDRESS_ID}", json=_address_in(city="Kraków"))
    assert resp.status_code == 200
    assert resp.json()["city"] == "Kraków"


def test_delete_address_not_found(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(address_repository, "delete", AsyncMock(return_value=False))
    resp = authed_client.delete(f"/me/addresses/{ADDRESS_ID}")
    assert resp.status_code == 404


def test_delete_address_happy_path(authed_client, fake_pool, monkeypatch):
    monkeypatch.setattr(address_repository, "delete", AsyncMock(return_value=True))
    resp = authed_client.delete(f"/me/addresses/{ADDRESS_ID}")
    assert resp.status_code == 204
