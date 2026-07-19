"""
Smoke test for the HTTP Basic Auth gate in app.py's before_request hook.
This is the exact regression the 2026-07-12 audit caught live (the gate was
silently deleted) — see CLAUDE.md's Design & Execution note.
"""

import base64


def test_unauthenticated_request_is_rejected(client):
    resp = client.get("/")
    assert resp.status_code == 401
    assert "Basic" in resp.headers.get("WWW-Authenticate", "")


def test_valid_credentials_are_accepted(client, auth_headers):
    resp = client.get("/", headers=auth_headers)
    assert resp.status_code == 200


def test_wrong_password_is_rejected(client):
    creds = base64.b64encode(b"testuser:wrongpass").decode()
    resp = client.get("/", headers={"Authorization": f"Basic {creds}"})
    assert resp.status_code == 401


def test_api_routes_are_gated_too(client):
    resp = client.get("/api/kpis")
    assert resp.status_code == 401
