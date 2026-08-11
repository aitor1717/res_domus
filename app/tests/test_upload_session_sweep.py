"""
Regression tests for the 2026-08-11 audit finding: api/upload.py's module-
level `_sessions` dict was only ever cleaned up by a successful
confirm-parse — a session started (date-confirmation prompt shown) and then
abandoned (tab closed, browser crash) stayed in the dict, and its saved
receipt images on disk, forever. `_sweep_expired_sessions` closes that gap;
these tests drive it directly rather than through the full SSE upload flow,
which needs a configured Anthropic key.

Also covers the follow-up ultrareview finding on the first version of that
fix: a single fixed TTL applied to every session regardless of state could
sweep a session still mid-review (parsed items shown, no UI timeout on how
long the user takes to confirm), and a sweep landing between confirm-parse's
session lookup and its slow save/import/archive work could rmtree the
session's images out from under it. Both are covered below.
"""

import time
from pathlib import Path

import pytest

import api.upload as upload_mod


@pytest.fixture(autouse=True)
def _clean_sessions():
    """_sessions is module-level state shared across tests in this file -
    clear it before and after each test so one test's fake sessions can't
    leak into another's."""
    upload_mod._sessions.clear()
    yield
    upload_mod._sessions.clear()


def _fake_session(group_dir: Path, created_at: float) -> dict:
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "00.jpg").write_bytes(b"fake")
    return {
        "sse_queue": None, "date_queue": None, "date_confirmed": False,
        "retry_queue": None, "images": [group_dir / "00.jpg"],
        "group_name": group_dir.name, "group_dir": group_dir,
        "items": None, "order_date": None, "created_at": created_at,
    }


def test_sweep_removes_expired_session_and_its_files(flask_app):
    upload_dir = Path(flask_app.config["UPLOAD_DIR"])
    expired_dir = upload_dir / "upload_expired1"
    fresh_dir = upload_dir / "upload_fresh01"

    upload_mod._sessions["expired-sid"] = _fake_session(
        expired_dir, created_at=0.0  # far in the past -> always expired
    )
    upload_mod._sessions["fresh-sid"] = _fake_session(
        fresh_dir, created_at=time.time()  # just created -> not expired
    )

    upload_mod._sweep_expired_sessions()

    assert "expired-sid" not in upload_mod._sessions
    assert not expired_dir.exists()

    assert "fresh-sid" in upload_mod._sessions
    assert fresh_dir.exists()


def test_sweep_is_a_noop_when_nothing_expired(flask_app):
    upload_dir = Path(flask_app.config["UPLOAD_DIR"])
    fresh_dir = upload_dir / "upload_fresh02"

    upload_mod._sessions["fresh-sid"] = _fake_session(
        fresh_dir, created_at=time.time()
    )
    upload_mod._sweep_expired_sessions()
    assert "fresh-sid" in upload_mod._sessions
    assert fresh_dir.exists()


def test_sweep_gives_a_reviewing_session_a_longer_grace_period(flask_app):
    """A session with parsed items (the user is looking at the review table)
    must survive well past SESSION_TTL_SECONDS - only SESSION_TTL_SECONDS
    applies to a session that never got that far."""
    upload_dir = Path(flask_app.config["UPLOAD_DIR"])
    reviewing_dir = upload_dir / "upload_reviewing1"
    abandoned_dir = upload_dir / "upload_abandoned1"

    now = time.time()
    reviewing = _fake_session(reviewing_dir, created_at=now - upload_mod.SESSION_TTL_SECONDS - 60)
    reviewing["items"] = [{"raw_name": "Milk"}]  # parsed - mid-review
    upload_mod._sessions["reviewing-sid"] = reviewing

    abandoned = _fake_session(abandoned_dir, created_at=now - upload_mod.SESSION_TTL_SECONDS - 60)
    # items stays None - never reached the review step
    upload_mod._sessions["abandoned-sid"] = abandoned

    upload_mod._sweep_expired_sessions()

    assert "reviewing-sid" in upload_mod._sessions
    assert reviewing_dir.exists()

    assert "abandoned-sid" not in upload_mod._sessions
    assert not abandoned_dir.exists()


def test_confirm_parse_claims_session_before_slow_work(client, auth_headers, flask_app, monkeypatch):
    """A concurrent sweep landing between confirm-parse's session lookup and
    its slow save/import/archive work used to be able to rmtree the
    session's group_dir mid-flight (run_import would already have committed
    purchases by then, and archive_images would then raise on the missing
    files). confirm-parse now pops the session immediately on lookup, so it
    is no longer visible to a concurrent sweep by the time any of that slow
    work starts - checked here by spying on save_review, the first slow step,
    and asserting the session is already gone from `_sessions` when it runs."""
    upload_dir = Path(flask_app.config["UPLOAD_DIR"])
    group_dir = upload_dir / "upload_race001"
    sid = "race-sid"

    sess = _fake_session(group_dir, created_at=time.time())
    sess["order_date"] = "2026-01-05"
    upload_mod._sessions[sid] = sess

    seen = {}
    real_save_review = upload_mod.save_review

    def _spy_save_review(*args, **kwargs):
        seen["session_still_present"] = sid in upload_mod._sessions
        return real_save_review(*args, **kwargs)

    monkeypatch.setattr(upload_mod, "save_review", _spy_save_review)

    resp = client.post(
        "/api/upload/confirm-parse",
        json={"session_id": sid, "items": []},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert seen["session_still_present"] is False
    assert sid not in upload_mod._sessions
