"""
Verifies the unit_price = total_price / quantity invariant (documented in
CLAUDE.md's Schema invariants) holds on manual edits via PATCH
/api/register/entries/<id> — the endpoint must recompute it server-side and
ignore whatever value the client sends, even if the client sends a bogus one.
"""

import sqlite3


def test_patch_recomputes_unit_price_and_ignores_client_value(client, auth_headers, seeded_db):
    conn = sqlite3.connect(seeded_db)
    entry_id = conn.execute("SELECT id FROM purchases LIMIT 1").fetchone()[0]
    conn.close()

    resp = client.patch(
        f"/api/register/entries/{entry_id}",
        json={"quantity": 4, "total_price": 23.30, "unit_price": 999},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["unit_price"] == round(23.30 / 4, 4)
    assert body["unit_price"] != 999


def test_patch_missing_entry_returns_404(client, auth_headers, seeded_db):
    resp = client.patch(
        "/api/register/entries/999999",
        json={"quantity": 1, "total_price": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_patch_requires_auth(client, seeded_db):
    resp = client.patch("/api/register/entries/1", json={"quantity": 1, "total_price": 1})
    assert resp.status_code == 401
