"""
Coverage for api/items.py (canonical item library CRUD over aux_items.csv) -
previously untested. Every test uses the aux_csv fixture (conftest.py) to
repoint AUX_CSV at an isolated file; api/items.py always writes straight to
disk under its own _csv_lock, with no DB or TEST_RUN isolation of its own.
"""


def test_list_items_returns_seeded_rows(client, auth_headers, aux_csv):
    resp = client.get("/api/items", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]["item"] == "Test Canon Item"


def test_list_items_filters_by_query(client, auth_headers, aux_csv):
    resp = client.get("/api/items?q=nomatch", headers=auth_headers)
    assert resp.get_json() == []

    resp = client.get("/api/items?q=canon", headers=auth_headers)
    assert len(resp.get_json()) == 1


def test_create_item_requires_name(client, auth_headers, aux_csv):
    resp = client.post("/api/items", json={"unit": "kg"}, headers=auth_headers)
    assert resp.status_code == 400


def test_create_item_writes_to_csv_and_increments_id(client, auth_headers, aux_csv):
    resp = client.post(
        "/api/items",
        json={"item": "New Item", "unit": "kg", "category": "Produce"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == "2"  # seeded row already holds id 1
    assert body["item"] == "New Item"

    listed = client.get("/api/items", headers=auth_headers).get_json()
    assert len(listed) == 2


def test_update_item_patches_fields(client, auth_headers, aux_csv):
    resp = client.patch("/api/items/1", json={"category": "Bakery"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["category"] == "Bakery"
    # unrelated fields untouched
    assert resp.get_json()["item"] == "Test Canon Item"


def test_update_item_missing_returns_404(client, auth_headers, aux_csv):
    resp = client.patch("/api/items/999", json={"category": "Bakery"}, headers=auth_headers)
    assert resp.status_code == 404


def test_delete_item_removes_row(client, auth_headers, aux_csv):
    resp = client.delete("/api/items/1", headers=auth_headers)
    assert resp.status_code == 200
    assert client.get("/api/items", headers=auth_headers).get_json() == []


def test_delete_item_missing_returns_404(client, auth_headers, aux_csv):
    resp = client.delete("/api/items/999", headers=auth_headers)
    assert resp.status_code == 404


def test_writes_require_auth(client, aux_csv):
    resp = client.post("/api/items", json={"item": "X"})
    assert resp.status_code == 401


def test_item_name_with_formula_trigger_char_is_safe_on_disk_and_clean_via_api(client, auth_headers, aux_csv):
    """Regression for the 2026-08-11 audit finding: aux_items.csv is
    user-editable free text written straight into a CSV cell with no
    formula-injection guard. A name starting with =/+/-/@ must (a) be
    written to the on-disk CSV with a protective leading quote, so opening
    aux_items.csv directly in Excel is safe, and (b) come back clean (no
    stray quote) through the API/UI, since _load() undoes the guard on
    read."""
    resp = client.post(
        "/api/items",
        json={"item": "=cmd|'/c calc'!A1", "unit": "u", "category": "Pantry"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.get_json()["item"] == "=cmd|'/c calc'!A1"  # clean through the API

    on_disk = aux_csv.read_text(encoding="utf-8")
    assert "'=cmd|" in on_disk  # guarded on disk

    listed = client.get("/api/items?q=cmd", headers=auth_headers).get_json()
    assert listed[0]["item"] == "=cmd|'/c calc'!A1"  # still clean after a fresh _load()


def test_writes_blocked_in_demo_mode(demo_client):
    resp = demo_client.post("/api/items", json={"item": "X"})
    assert resp.status_code == 403
